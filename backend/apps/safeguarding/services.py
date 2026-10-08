"""
Safeguarding check: is every cedi of e-money backed by a cedi in trust?

  liabilities = customer wallets + agent float + interop transfers still in flight
                (all e-money SokoPay owes, taken straight from the ledger)
  trust       = latest balance of every active trust account

  surplus = trust − liabilities  → must be ≥ 0.

A shortfall, a missing balance, or a balance older than TRUST_BALANCE_MAX_AGE is
logged at CRITICAL so monitoring pages the finance and compliance owners.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db.models import Q, Sum
from django.utils import timezone

from apps.ledger.models import AccountBalance

from .models import SafeguardingCheck, TrustAccount, TrustBalance

logger = logging.getLogger("sokopay.safeguarding")

# Bank balances older than this are not trusted for the check.
TRUST_BALANCE_MAX_AGE = dt.timedelta(hours=36)

# Ledger accounts that represent e-money owed to customers/agents.
E_MONEY_PREFIXES = ("customer_wallet:", "agent_float:")
E_MONEY_EXACT = ("interop_in_flight", "cross_border_in_flight", "lifestyle_in_flight")


def e_money_liabilities(currency: str = "GHS") -> int:
    q = Q(account__code__in=E_MONEY_EXACT)
    for prefix in E_MONEY_PREFIXES:
        q |= Q(account__code__startswith=prefix)
    raw = AccountBalance.objects.filter(q, account__currency_id=currency) \
        .aggregate(total=Sum("balance"))["total"] or 0
    # These are liabilities: their natural (owed) balance is the negated raw balance.
    return -int(raw)


def record_trust_balance(*, account: TrustAccount, balance_minor: int, as_of,
                         actor=None, source: str = TrustBalance.Source.MANUAL) -> TrustBalance:
    return TrustBalance.objects.create(account=account, balance_minor=balance_minor,
                                       as_of=as_of, source=source, recorded_by=actor)


def run_check() -> SafeguardingCheck:
    liabilities = e_money_liabilities()
    accounts = list(TrustAccount.objects.filter(is_active=True))
    now = timezone.now()

    latest = {
        a.id: a.balances.order_by("-as_of").first()
        for a in accounts
    }
    detail = {
        "accounts": [
            {"account": str(a), "balance_minor": b.balance_minor if b else None,
             "as_of": b.as_of.isoformat() if b else None}
            for a, b in ((a, latest[a.id]) for a in accounts)
        ]
    }

    if not accounts or any(b is None for b in latest.values()):
        # Nothing to safeguard yet (e.g. operating under a PSP licence) is fine;
        # holding e-money with no trust balance on record is not.
        status = SafeguardingCheck.Status.OK if liabilities == 0 else SafeguardingCheck.Status.NO_DATA
        check = SafeguardingCheck.objects.create(
            liabilities_minor=liabilities, status=status, detail=detail)
    else:
        trust_total = sum(b.balance_minor for b in latest.values())
        surplus = trust_total - liabilities
        stale = any(now - b.as_of > TRUST_BALANCE_MAX_AGE for b in latest.values())
        if surplus < 0:
            status = SafeguardingCheck.Status.SHORTFALL
        elif stale:
            status = SafeguardingCheck.Status.STALE
        else:
            status = SafeguardingCheck.Status.OK
        check = SafeguardingCheck.objects.create(
            liabilities_minor=liabilities, trust_total_minor=trust_total,
            surplus_minor=surplus, status=status, detail=detail)

    if check.status != SafeguardingCheck.Status.OK:
        logger.critical("Safeguarding check %s: liabilities=%s trust=%s surplus=%s",
                        check.status, check.liabilities_minor, check.trust_total_minor,
                        check.surplus_minor)
    return check
