"""
Prices in force, maker-checker changes, and agent commissions.

  fee(product, amount)          what SokoPay charges (rule in force, else DEFAULTS)
  commission(product, amount)   what an agent earns
  propose(...) / decide(...)    a price change: one finance user proposes, ANOTHER approves;
                                never retroactive (approval moves a past start to "now")
  accrue_commission(txn, ...)   on each cash-in / cash-out:
                                    Dr commission_expense   Cr agent_commission:<agent>
  pay_commissions()             monthly: move accrued commission into the agent's float
                                    Dr agent_commission:<agent>   Cr agent_float:<agent>

Abuse controls on commission: none on an agent's own wallet; none below
COMMISSION_MIN_TXN_MINOR (tiny cash-in/out cycles just to farm commission); suspended
agents' commission is held, not paid; AML's AGENT_CYCLE rule watches the pattern.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.common.audit import audited

from .models import AgentCommission, PriceRule

MAX_PERCENT_BP = 1000          # 10%: a typo guard, not a business limit
MAX_FLAT_MINOR = 100_00        # GH₵100
COMMISSION_MIN_TXN_MINOR = 5_00


class PricingError(ValueError):
    pass


def _bulk_default(product: str, amount: int) -> int:
    from apps.bulk.fees import BULK_FEE_BP_DEFAULT, BULK_FEE_MIN_MINOR_DEFAULT
    bp = getattr(settings, "BULK_FEE_BP", BULK_FEE_BP_DEFAULT)
    floor = getattr(settings, "BULK_FEE_MIN_MINOR", BULK_FEE_MIN_MINOR_DEFAULT)
    return max((amount * bp + 5000) // 10000, floor)


# Code defaults (used until a rule is approved) — the prices the platform launched with.
DEFAULTS = {
    ("fee", "bill"): lambda a: 50,              # GH₵0.50 flat [VERIFY]
    ("fee", "airtime"): lambda a: 0,
    ("fee", "data"): lambda a: 0,
    ("fee", "cash_out"): lambda a: 0,           # [VERIFY] set the cash-out tariff before launch
    ("fee", "cash_in"): lambda a: 0,
    ("fee", "bulk_momo"): lambda a: _bulk_default("bulk_momo", a),
    ("fee", "bulk_bank"): lambda a: _bulk_default("bulk_bank", a),
    ("commission", "cash_in"): lambda a: 0,     # [VERIFY] agent commission schedule
    ("commission", "cash_out"): lambda a: 0,
}


def rule_in_force(kind: str, product: str, at=None) -> PriceRule | None:
    at = at or timezone.now()
    return (PriceRule.objects.filter(kind=kind, product=product, status=PriceRule.Status.APPROVED,
                                     effective_from__lte=at)
            .order_by("-effective_from", "-decided_at").first())


def _price(kind: str, product: str, amount_minor: int, at=None) -> tuple[int, PriceRule | None]:
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")
    rule = rule_in_force(kind, product, at)
    if rule is not None:
        return rule.amount_for(amount_minor), rule
    default = DEFAULTS.get((kind, product))
    return (min(default(amount_minor), amount_minor) if default else 0), None


def fee(product: str, amount_minor: int, at=None) -> int:
    return _price("fee", product, amount_minor, at)[0]


def commission(product: str, amount_minor: int, at=None) -> tuple[int, PriceRule | None]:
    return _price("commission", product, amount_minor, at)


# --- maker-checker ------------------------------------------------------------------------
@audited("pricing.propose", actor="proposed_by", fields=("kind", "product", "flat_minor", "percent_bp", "min_minor", "max_minor", "note"))
def propose(*, kind: str, product: str, flat_minor: int = 0, percent_bp: int = 0, min_minor: int = 0,
            max_minor: int = 0, effective_from=None, note: str, proposed_by) -> PriceRule:
    if kind not in PriceRule.Kind.values or product not in PriceRule.Product.values:
        raise PricingError("Unknown price type.")
    if (kind, product) not in DEFAULTS:
        raise PricingError("That combination isn't priced.")
    note = (note or "").strip()
    if len(note) < 5:
        raise PricingError("Say why the price is changing (kept on the audit trail).")
    values = [flat_minor, percent_bp, min_minor, max_minor]
    if any(not isinstance(v, int) or v < 0 for v in values):
        raise PricingError("Amounts must be zero or more.")
    if percent_bp > MAX_PERCENT_BP or flat_minor > MAX_FLAT_MINOR:
        raise PricingError("That price is unusually high. Check the numbers (max 10% or GH₵100 flat).")
    if max_minor and max_minor < min_minor:
        raise PricingError("The maximum is below the minimum.")
    now = timezone.now()
    effective_from = effective_from or now
    if effective_from < now - timedelta(minutes=1):
        raise PricingError("Prices can't start in the past.")
    return PriceRule.objects.create(kind=kind, product=product, flat_minor=flat_minor, percent_bp=percent_bp,
                                    min_minor=min_minor, max_minor=max_minor, effective_from=effective_from,
                                    note=note[:255], proposed_by=proposed_by)


@audited("pricing.decide", actor="approver", fields=("approve",))
def decide(rule: PriceRule, *, approver, approve: bool) -> PriceRule:
    with transaction.atomic():
        r = PriceRule.objects.select_for_update().get(pk=rule.pk)
        if r.status != PriceRule.Status.PENDING:
            raise PricingError("This price change has already been decided.")
        if approver.id == r.proposed_by_id:
            raise PricingError("A different person must approve a price change.")
        now = timezone.now()
        r.status = PriceRule.Status.APPROVED if approve else PriceRule.Status.REJECTED
        if approve and r.effective_from < now:
            r.effective_from = now                  # never retroactive
        r.decided_by, r.decided_at = approver, now
        r.save(update_fields=["status", "effective_from", "decided_by", "decided_at", "updated_at"])
    return r


# --- agent commissions ----------------------------------------------------------------------
def accrue_commission(*, txn, product: str, customer=None) -> AgentCommission | None:
    """Call inside the cash-in / cash-out transaction, after the money moved."""
    agent = txn.agent
    if txn.amount_minor < COMMISSION_MIN_TXN_MINOR:
        return None
    if customer is not None and customer.id == agent.user_id:
        return None                                           # no commission on yourself
    amount, rule = commission(product, txn.amount_minor)
    if amount <= 0:
        return None
    cur = txn.currency
    post_entry(f"Agent commission ({product})",
               [debit(accounts.commission_expense(cur), amount),
                credit(accounts.agent_commission(str(agent.id), cur), amount)],
               idempotency_key=f"commission:{txn.id}", reference=("agent_commission", str(agent.id)))
    return AgentCommission.objects.create(agent=agent, txn=txn, product=product, amount_minor=amount, rule=rule)


def unpaid_minor(agent) -> int:
    return int(AgentCommission.objects.filter(agent=agent, paid_at=None).aggregate(s=Sum("amount_minor"))["s"] or 0)


def pay_commissions() -> dict:
    """Pay accrued commission into each ACTIVE agent's float (suspended agents: held)."""
    from apps.agents.models import Agent
    from apps.notifications.services import ghs, notify
    paid_agents, total = 0, 0
    for agent in Agent.objects.filter(status=Agent.Status.ACTIVE, commissions__paid_at=None).distinct():
        with transaction.atomic():
            rows = list(AgentCommission.objects.select_for_update().filter(agent=agent, paid_at=None))
            amount = sum(r.amount_minor for r in rows)
            if amount <= 0:
                continue
            now = timezone.now()
            post_entry(f"Agent commission payout {now:%Y-%m}",
                       [debit(accounts.agent_commission(str(agent.id)), amount),
                        credit(accounts.agent_float(str(agent.id)), amount)],
                       idempotency_key=f"commission-pay:{agent.id}:{rows[-1].id}",
                       reference=("agent_commission", str(agent.id)))
            AgentCommission.objects.filter(pk__in=[r.pk for r in rows]).update(paid_at=now)
            notify(agent.user, kind="payment", title="Commission paid", app="agent",
                   body=f"{ghs(amount)} commission has been added to your float.", data={"type": "float"})
        paid_agents += 1
        total += amount
    return {"agents_paid": paid_agents, "total_minor": total}
