"""
Prices in force, maker-checker changes, and agent commissions.

  fee(product, amount)          what SokoPay charges (rule in force, else DEFAULTS), less any
                                active approved promotion
  quote(product, amount)        the same, itemised for the app: base fee, discount, promotion
  commission(product, amount)   what an agent earns
  propose(...) / decide(...)    a price change: one finance user proposes, ANOTHER approves;
                                never retroactive (approval moves a past start to "now")
  accrue_commission(txn, ...)   on each cash-in / cash-out:
                                    Dr commission_expense   Cr agent_commission:<agent>
  redeem_commission(agent)      the agent moves cash-in / sales commission into their float, any time
  prepare_payout / decide_payout  fortnightly: finance pays cash-out commission (two people)
                                    both: Dr agent_commission:<agent>   Cr agent_float:<agent>

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

from .models import AgentCommission, CommissionPayout, PriceRule, Promotion

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
    ("fee", "p2p"): lambda a: 0,                # free until finance sets a price
    ("fee", "transfer_momo"): lambda a: 0,
    ("fee", "transfer_bank"): lambda a: 0,
    ("fee", "bill"): lambda a: 50,              # GH₵0.50 flat [VERIFY]
    ("fee", "airtime"): lambda a: 0,
    ("fee", "data"): lambda a: 0,
    ("fee", "cash_out"): lambda a: 0,           # [VERIFY] set the cash-out tariff before launch
    ("fee", "cash_in"): lambda a: 0,
    ("fee", "bulk_momo"): lambda a: _bulk_default("bulk_momo", a),
    ("fee", "bulk_bank"): lambda a: _bulk_default("bulk_bank", a),
    ("commission", "cash_in"): lambda a: 0,     # [VERIFY] agent commission schedule
    ("commission", "cash_out"): lambda a: 0,
    ("commission", "airtime"): lambda a: 0,     # agents selling airtime / data / bills for customers
    ("commission", "data"): lambda a: 0,
    ("commission", "bill"): lambda a: 0,
}
FEE_PRODUCTS = [p for k, p in DEFAULTS if k == "fee"]
MAX_PROMOTION_DAYS = 366


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


def active_promotion(product: str, at=None) -> Promotion | None:
    """The approved promotion giving the biggest discount on `product` right now (or None)."""
    at = at or timezone.now()
    return (Promotion.objects.filter(status=Promotion.Status.APPROVED, starts_at__lte=at, ends_at__gt=at,
                                     product__in=(product, Promotion.ALL))
            .order_by("-percent_off_bp", "ends_at").first())


def quote(product: str, amount_minor: int, at=None) -> dict:
    """Itemised fee: {"base", "discount", "fee", "promotion"} in pesewas (promotion = name or "")."""
    base, _rule = _price("fee", product, amount_minor, at)
    promo = active_promotion(product, at) if base else None
    discount = promo.discount_on(base) if promo else 0
    return {"base": base, "discount": discount, "fee": base - discount, "promotion": promo.name if promo else ""}


def fee(product: str, amount_minor: int, at=None) -> int:
    return quote(product, amount_minor, at)["fee"]


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
def accrue_commission(*, txn, product: str, customer=None, on_minor: int | None = None) -> AgentCommission | None:
    """
    Call inside the cash-in / cash-out / sale transaction, after the money moved. `on_minor`:
    the value commission is earned on when it isn't the txn amount (a sale: face value, not
    the customer's fee).
    """
    agent = txn.agent
    base = txn.amount_minor if on_minor is None else on_minor
    if base < COMMISSION_MIN_TXN_MINOR:
        return None
    if customer is not None and customer.id == agent.user_id:
        return None                                           # no commission on yourself
    amount, rule = commission(product, base)
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


PAYOUT_PRODUCTS = ("cash_out",)          # paid by SokoPay every two weeks
PAYOUT_INTERVAL_DAYS = 14


def earnings(agent) -> dict:
    """What the Agent app shows: redeemable now, waiting for the fortnightly payout, paid so far."""
    unpaid = AgentCommission.objects.filter(agent=agent, paid_at=None)
    redeemable = unpaid.exclude(product__in=PAYOUT_PRODUCTS)
    awaiting = unpaid.filter(product__in=PAYOUT_PRODUCTS)

    def total(qs):
        return int(qs.aggregate(s=Sum("amount_minor"))["s"] or 0)
    last = CommissionPayout.objects.filter(status=CommissionPayout.Status.PAID).order_by("-decided_at").first()
    next_due = (last.decided_at if last else None)
    return {
        "redeemable_minor": total(redeemable),
        "awaiting_payout_minor": total(awaiting),
        "paid_minor": total(AgentCommission.objects.filter(agent=agent).exclude(paid_at=None)),
        "next_payout_due": (next_due + timedelta(days=PAYOUT_INTERVAL_DAYS)) if next_due else None,
    }


@audited("commission.redeem", actor="actor", obj="agent")
def redeem_commission(agent, *, actor) -> int:
    """Move every redeemable (non-cash-out) unpaid commission into the agent's float. Returns pesewas."""
    from apps.agents.models import Agent
    from apps.notifications.services import ghs, notify
    if agent.status != Agent.Status.ACTIVE:
        raise PricingError("Your agent account isn't active, so earnings are held. Contact SokoPay.")
    with transaction.atomic():
        rows = list(AgentCommission.objects.select_for_update().filter(agent=agent, paid_at=None)
                    .exclude(product__in=PAYOUT_PRODUCTS).order_by("created_at"))
        amount = sum(r.amount_minor for r in rows)
        if amount <= 0:
            raise PricingError("There's nothing to redeem yet.")
        now = timezone.now()
        post_entry("Agent commission redeemed",
                   [debit(accounts.agent_commission(str(agent.id)), amount),
                    credit(accounts.agent_float(str(agent.id)), amount)],
                   idempotency_key=f"commission-redeem:{agent.id}:{rows[-1].id}",
                   reference=("agent_commission", str(agent.id)))
        AgentCommission.objects.filter(pk__in=[r.pk for r in rows]).update(
            paid_at=now, settled_via=AgentCommission.Settled.REDEEMED)
    notify(agent.user, kind="payment", title="Earnings redeemed", app="agent",
           body=f"{ghs(amount)} of earnings is now in your float.", data={"type": "float"})
    return amount


@audited("commission.payout_prepare", actor="prepared_by")
def prepare_payout(*, prepared_by, period_end=None) -> CommissionPayout:
    """Maker: gather every unpaid cash-out commission up to `period_end` into a payout batch."""
    period_end = period_end or timezone.now()
    if CommissionPayout.objects.filter(status=CommissionPayout.Status.PENDING).exists():
        raise PricingError("A payout is already waiting for approval. Approve or reject it first.")
    with transaction.atomic():
        rows = list(AgentCommission.objects.select_for_update()
                    .filter(paid_at=None, payout=None, product__in=PAYOUT_PRODUCTS, created_at__lte=period_end))
        if not rows:
            raise PricingError("There's no unpaid cash-out commission to pay.")
        payout = CommissionPayout.objects.create(
            period_end=period_end, total_minor=sum(r.amount_minor for r in rows),
            agent_count=len({r.agent_id for r in rows}), prepared_by=prepared_by)
        AgentCommission.objects.filter(pk__in=[r.pk for r in rows]).update(payout=payout)
    return payout


@audited("commission.payout_decide", actor="approver", fields=("approve",))
def decide_payout(payout: CommissionPayout, *, approver, approve: bool) -> CommissionPayout:
    """Checker: a DIFFERENT finance user pays it (each active agent's float credited) or rejects it."""
    from apps.agents.models import Agent
    from apps.notifications.services import ghs, notify
    with transaction.atomic():
        p = CommissionPayout.objects.select_for_update().get(pk=payout.pk)
        if p.status != CommissionPayout.Status.PENDING:
            raise PricingError("This payout has already been decided.")
        if approver.id == p.prepared_by_id:
            raise PricingError("A different finance user must approve the payout.")
        now = timezone.now()
        rows = list(AgentCommission.objects.select_for_update().filter(payout=p, paid_at=None)
                    .select_related("agent"))
        if not approve:
            AgentCommission.objects.filter(pk__in=[r.pk for r in rows]).update(payout=None)
            p.status = CommissionPayout.Status.REJECTED
        else:
            by_agent: dict = {}
            for r in rows:
                by_agent.setdefault(r.agent, []).append(r)
            paid_total, held = 0, []
            for agent, items in by_agent.items():
                if agent.status != Agent.Status.ACTIVE:          # suspended agents: held for the next payout
                    held += [r.pk for r in items]
                    continue
                amount = sum(r.amount_minor for r in items)
                post_entry(f"Agent commission payout {now:%Y-%m-%d}",
                           [debit(accounts.agent_commission(str(agent.id)), amount),
                            credit(accounts.agent_float(str(agent.id)), amount)],
                           idempotency_key=f"commission-payout:{p.id}:{agent.id}",
                           reference=("agent_commission", str(agent.id)))
                AgentCommission.objects.filter(pk__in=[r.pk for r in items]).update(
                    paid_at=now, settled_via=AgentCommission.Settled.PAYOUT)
                paid_total += amount
                transaction.on_commit(lambda a=agent, m=amount: notify(
                    a.user, kind="payment", title="Cash-out earnings paid", app="agent",
                    body=f"{ghs(m)} of cash-out earnings has been added to your float.", data={"type": "float"}))
            if held:
                AgentCommission.objects.filter(pk__in=held).update(payout=None)
            p.status = CommissionPayout.Status.PAID
            p.total_minor = paid_total
        p.decided_by, p.decided_at = approver, now
        p.save(update_fields=["status", "total_minor", "decided_by", "decided_at", "updated_at"])
    return p


def pay_commissions() -> dict:
    """
    Legacy all-at-once payout (no longer scheduled): kept for operations scripts only. Pays
    every accrued commission into each ACTIVE agent's float (suspended agents: held).
    """
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



# --- promotions (time-limited discounts on fees, maker-checker) ------------------------------
@audited("pricing.promotion_propose", actor="proposed_by", fields=("name", "product", "percent_off_bp", "note"))
def propose_promotion(*, name: str, product: str, percent_off_bp: int, starts_at, ends_at, note: str,
                      proposed_by) -> Promotion:
    name, note = (name or "").strip(), (note or "").strip()
    if not 3 <= len(name) <= 80:
        raise PricingError("Give the discount a short name customers will see (3–80 characters).")
    if product != Promotion.ALL and product not in FEE_PRODUCTS:
        raise PricingError("Choose a fee, or every fee.")
    if not isinstance(percent_off_bp, int) or not 100 <= percent_off_bp <= 10_000:
        raise PricingError("The discount must be between 1% and 100% (free).")
    if len(note) < 5:
        raise PricingError("Say why (kept on the audit trail).")
    now = timezone.now()
    if starts_at is None or ends_at is None:
        raise PricingError("Set when the discount starts and ends.")
    if ends_at <= starts_at:
        raise PricingError("The end must be after the start.")
    if ends_at <= now:
        raise PricingError("That period is already over.")
    if (ends_at - starts_at).days > MAX_PROMOTION_DAYS:
        raise PricingError("A discount can run for at most a year. Use a price change for longer.")
    return Promotion.objects.create(name=name, product=product, percent_off_bp=percent_off_bp,
                                    starts_at=max(starts_at, now - timedelta(minutes=1)), ends_at=ends_at,
                                    note=note[:255], proposed_by=proposed_by)


@audited("pricing.promotion_decide", actor="approver", fields=("approve",))
def decide_promotion(promo: Promotion, *, approver, approve: bool) -> Promotion:
    with transaction.atomic():
        p = Promotion.objects.select_for_update().get(pk=promo.pk)
        if p.status != Promotion.Status.PENDING:
            raise PricingError("This discount has already been decided.")
        if approver.id == p.proposed_by_id:
            raise PricingError("A different person must approve a discount.")
        now = timezone.now()
        if approve and p.ends_at <= now:
            raise PricingError("This discount's period is already over. Propose a new one.")
        p.status = Promotion.Status.APPROVED if approve else Promotion.Status.REJECTED
        if approve and p.starts_at < now:
            p.starts_at = now                          # never retroactive
        p.decided_by, p.decided_at = approver, now
        p.save(update_fields=["status", "starts_at", "decided_by", "decided_at", "updated_at"])
    return p


@audited("pricing.promotion_end", actor="actor", fields=("reason",))
def end_promotion(promo: Promotion, *, actor, reason: str) -> Promotion:
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise PricingError("Say why the discount is ending early.")
    with transaction.atomic():
        p = Promotion.objects.select_for_update().get(pk=promo.pk)
        now = timezone.now()
        if p.status != Promotion.Status.APPROVED or p.ends_at <= now:
            raise PricingError("Only a running or upcoming approved discount can be ended.")
        p.status = Promotion.Status.ENDED
        p.ends_at = max(now, p.starts_at)
        p.ended_by, p.ended_reason = actor, reason[:255]
        p.save(update_fields=["status", "ends_at", "ended_by", "ended_reason", "updated_at"])
    return p
