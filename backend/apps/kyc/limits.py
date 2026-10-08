"""
Wallet limit enforcement.

Called by every operation that moves e-money into or out of a customer wallet:

  money IN  (top-up, P2P received, agent cash-in, remittance, loan disbursement)
      → check_credit: per-transaction limit and the balance cap
  money OUT (P2P sent, interop transfer, agent cash-out, paying from the wallet,
             insurance premium)
      → check_debit: compliance hold, per-transaction, daily and monthly limits

Usage is measured from the ledger itself (debit postings on the wallet account), so it
can't drift from what actually happened. Days and months follow Ghana time (GMT).
Reserved interop transfers that were later refunded still count toward the day's
outflow — deliberately conservative.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.common.money import Money
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import natural_balance_of

from .exceptions import LimitExceeded, WalletFrozen
from .models import KycProfile


def profile_for(user) -> KycProfile:
    profile, _ = KycProfile.objects.get_or_create(user=user)
    return profile


def tier_limits(tier: int) -> dict:
    return settings.KYC_TIER_LIMITS[int(tier)]


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


def outflow_since(user, since: dt.datetime, currency: str = "GHS") -> int:
    """Total money that has left the user's wallet since `since` (minor units)."""
    wallet = accounts.customer_wallet(str(user.id), currency)
    # A debit (positive amount) on a liability wallet is money OUT.
    total = Posting.objects.filter(
        account=wallet, amount__gt=0, created_at__gte=since
    ).aggregate(total=Sum("amount"))["total"]
    return int(total or 0)


def _day_start() -> dt.datetime:
    now = timezone.localtime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start() -> dt.datetime:
    return _day_start().replace(day=1)


def usage(user) -> dict:
    return {"daily_out": outflow_since(user, _day_start()),
            "monthly_out": outflow_since(user, _month_start())}


def check_credit(user, amount_minor: int, currency: str = "GHS") -> None:
    limits = tier_limits(profile_for(user).tier)
    if limits["max_txn"] is not None and amount_minor > limits["max_txn"]:
        raise LimitExceeded(
            f"This is above your per-transaction limit of {_ghs(limits['max_txn'])}. "
            "Verify your Ghana Card to raise it."
        )
    balance = natural_balance_of(accounts.customer_wallet(str(user.id), currency))
    if limits["max_balance"] is not None and balance + amount_minor > limits["max_balance"]:
        raise LimitExceeded(
            f"This would take the wallet above its {_ghs(limits['max_balance'])} limit. "
            "Verify your Ghana Card to raise it."
        )


def check_debit(user, amount_minor: int) -> None:
    profile = profile_for(user)
    if profile.frozen:
        raise WalletFrozen("Your wallet is on hold. Please contact SokoPay support.")
    limits = tier_limits(profile.tier)
    if limits["max_txn"] is not None and amount_minor > limits["max_txn"]:
        raise LimitExceeded(
            f"This is above your per-transaction limit of {_ghs(limits['max_txn'])}."
        )
    used = usage(user)
    # SIM-swap protection: for 24 h after a forgot-PIN reset, sends are capped low.
    reset_at = getattr(user, "pin_reset_at", None)
    if reset_at is not None and reset_at > timezone.now() - dt.timedelta(hours=24):
        cap = int(getattr(settings, "PIN_RESET_COOLDOWN_DAILY_MINOR", 500_00))
        spent_since = outflow_since(user, reset_at)
        if spent_since + amount_minor > cap:
            raise LimitExceeded(
                f"For your security, you can send up to {_ghs(cap)} in the 24 hours after a PIN reset."
            )
    if limits["daily_out"] is not None and used["daily_out"] + amount_minor > limits["daily_out"]:
        raise LimitExceeded(f"This would exceed your daily limit of {_ghs(limits['daily_out'])}.")
    if (limits["monthly_out"] is not None
            and used["monthly_out"] + amount_minor > limits["monthly_out"]):
        raise LimitExceeded(
            f"This would exceed your monthly limit of {_ghs(limits['monthly_out'])}."
        )


def format_limit(minor) -> str:
    return "Unlimited" if minor is None else _ghs(minor)
