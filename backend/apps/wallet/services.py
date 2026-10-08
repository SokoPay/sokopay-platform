"""
Consumer wallet services (a DEMI-licence feature).

A customer wallet is e-money SokoPay holds for a customer — the ledger account
customer_wallet:<user_id>. Holding it requires the DEMI licence, so every operation
here is gated:
  * fund a wallet / receive e-money   → HOLD_CUSTOMER_FUNDS
  * send wallet-to-wallet (P2P)        → WALLET_P2P

The wallet's transaction history is simply the ledger postings on the wallet account,
so there is no separate history table to keep in sync.

Funding a wallet reuses the payments collection + secure webhook machinery: we create a
Payment with purpose WALLET_FUND, collect from the customer's MoMo, and on confirmed
success credit the wallet (see confirm_funding, called by the payments webhook handler).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.common.money import Money
from apps.kyc import limits as kyc_limits
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.payments.models import Payment
from apps.payments.reference import new_reference
from apps.rails.registry import get_rail
from apps.rails.types import CollectionRequest, Network, RailStatus

from .exceptions import WalletError

User = get_user_model()


# --- balance & history ------------------------------------------------------
def balance(user, currency: str = "GHS") -> int:
    return natural_balance_of(accounts.customer_wallet(str(user.id), currency))


def history(user, currency: str = "GHS", limit: int = 50) -> list[dict]:
    """Recent wallet movements, derived from the ledger postings on the wallet account."""
    acct = accounts.customer_wallet(str(user.id), currency)
    rows = (
        Posting.objects.filter(account=acct)
        .select_related("entry")
        .order_by("-created_at")[:limit]
    )
    out = []
    for p in rows:
        # Wallet is a liability: a credit posting (amount < 0) is money IN.
        money_in = p.amount < 0
        out.append({
            "direction": "in" if money_in else "out",
            "amount_minor": abs(p.amount),
            "narrative": p.entry.narrative,
            "at": p.created_at,
        })
    return out


# --- funding (MoMo → wallet) ------------------------------------------------
def initiate_funding(*, user, amount_minor: int, network: Network, payer: str | None = None,
                     idempotency_key: str | None = None) -> Payment:
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    if amount_minor <= 0:
        raise WalletError("Amount must be positive.")

    # Idempotency keys are scoped to the user, so one customer replaying another's key
    # can never be handed someone else's payment.
    if idempotency_key:
        idempotency_key = f"u:{user.id}:{idempotency_key}"
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # Tier limits are checked when the top-up starts. The money arrives later (after the
    # customer approves on their phone) and is credited even if usage changed in between:
    # once collected, refusing it would only strand the customer's money.
    kyc_limits.check_credit(user, amount_minor)

    currency = settings.DEFAULT_CURRENCY
    payer = payer or user.phone
    payment = Payment.objects.create(
        reference=new_reference(),
        purpose=Payment.Purpose.WALLET_FUND,
        status=Payment.Status.CREATED,
        user=user,
        amount_minor=amount_minor,
        fee_minor=0,
        total_minor=amount_minor,
        currency=currency,
        network=str(network),
        payer=payer,
        rail=getattr(settings, "RAIL_PROVIDER", "mock"),
        idempotency_key=idempotency_key,
    )
    rail = get_rail()
    result = rail.collect(CollectionRequest(
        amount=Money(amount_minor, currency),
        network=Network(network),
        payer=payer,
        reference=payment.reference,
        narrative="Wallet top-up",
        idempotency_key=f"collect-init:{payment.id}",
    ))
    payment.rail_ref = result.provider_ref
    if result.status == RailStatus.FAILED:
        payment.status = Payment.Status.FAILED
        payment.failure_code = result.failure_code or "rail_rejected"
        payment.completed_at = timezone.now()
    else:
        payment.status = Payment.Status.PENDING
    payment.save(update_fields=["rail_ref", "status", "failure_code", "completed_at", "updated_at"])
    return payment


@transaction.atomic
def confirm_funding(payment: Payment) -> Payment:
    """Credit the customer's wallet once a WALLET_FUND collection has succeeded."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_terminal:
        return payment
    cur = payment.currency
    clearing = accounts.partner_clearing(payment.rail, cur)
    wallet = accounts.customer_wallet(str(payment.user_id), cur)
    post_entry(
        f"Wallet top-up {payment.reference}",
        [debit(clearing, payment.amount_minor), credit(wallet, payment.amount_minor)],
        idempotency_key=f"walletfund:{payment.id}",
        reference=("payment", str(payment.id)),
    )
    payment.status = Payment.Status.SUCCEEDED
    payment.completed_at = timezone.now()
    payment.save(update_fields=["status", "completed_at", "updated_at"])
    notify(payment.user, kind="wallet", title="Wallet topped up",
           body=f"{ghs(payment.amount_minor)} added to your SokoPay wallet.",
           data={"type": "wallet", "reference": payment.reference}, app="customer")
    return payment


# --- person-to-person -------------------------------------------------------
def display_name(user) -> str:
    """'Ama M.' — enough for the sender to recognise the recipient, no more."""
    parts = (user.full_name or "").split()
    if not parts:
        return "SokoPay user"
    return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")


def lookup_recipient(phone: str) -> dict:
    """Who would receive a SokoPay-to-SokoPay transfer? Never creates an account."""
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None:
        return {"found": False, "name": ""}
    return {"found": True, "name": display_name(user)}


@transaction.atomic
def send_p2p(*, sender, recipient_phone: str, amount_minor: int, currency: str = "GHS") -> dict:
    """Move e-money from one wallet to another. Sender cannot go negative."""
    require_capability(Capability.WALLET_P2P)
    if amount_minor <= 0:
        raise WalletError("Amount must be positive.")
    if recipient_phone == sender.phone:
        raise WalletError("You cannot send to yourself.")

    # The recipient must already be a SokoPay user. Creating a wallet for a mistyped
    # number would strand the money with a stranger who may never claim it.
    recipient = User.objects.filter(phone=recipient_phone, is_active=True).first()
    if recipient is None:
        raise WalletError("No SokoPay account for that number. To pay a mobile money "
                          "wallet, use Send money instead.")
    if balance(sender, currency) < amount_minor:
        raise WalletError("Insufficient wallet balance.")
    kyc_limits.check_debit(sender, amount_minor)
    kyc_limits.check_credit(recipient, amount_minor, currency)
    sender_acc = accounts.customer_wallet(str(sender.id), currency)
    recipient_acc = accounts.customer_wallet(str(recipient.id), currency)
    post_entry(
        f"Transfer {sender.phone} → {recipient_phone}",
        [debit(sender_acc, amount_minor), credit(recipient_acc, amount_minor)],
        reference=("p2p", str(sender.id)),
    )
    notify(recipient, kind="wallet", title="Money received",
           body=f"{ghs(amount_minor)} from {display_name(sender)} is in your wallet.",
           data={"type": "wallet"}, app="customer")
    return {"recipient": recipient_phone, "recipient_name": display_name(recipient),
            "amount_minor": amount_minor, "new_balance_minor": balance(sender, currency)}
