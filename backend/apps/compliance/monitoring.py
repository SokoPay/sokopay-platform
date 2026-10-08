"""
Transaction monitoring rules.

Runs after every committed ledger entry (via apps.ledger.hooks) on the customer wallets
and agent floats it touched, plus a daily batch for patterns that need a full day.
Every threshold is a setting (AML_RULES) — compliance tunes them, not engineers.
[VERIFY thresholds with the MLRO and the FIC's current guidance before go-live.]

Customer rules (wallet movements):
  LARGE_TXN             movement >= reporting threshold → LargeTransactionReport (a filing, not a suspicion)
  VELOCITY_OUT          many outgoing movements in a short window
  STRUCTURING           repeated amounts just under a threshold
  PASS_THROUGH          money in, then (nearly) all of it straight out — mule pattern
  MANY_SENDERS          many different people sending to one wallet — mule / collection account
  NEW_ACCOUNT_HIGH_OUT  big outflows from a days-old account
  DORMANT_REACTIVATION  long-silent account suddenly sends a large amount
  NEW_DEVICE_HIGH_OUT   new phone signed in, then large outflow — possible account takeover
Agent rules (cash):
  AGENT_CASH_CYCLING    cash-in and cash-out for the same customer at the same agent, same day
  AGENT_SPLIT_CASHOUT   one customer's cash-out split into several at the same agent
Merchant rules (daily):
  MERCHANT_SPIKE        a day's collections far above the merchant's normal
"""

from __future__ import annotations

import logging
import datetime as dt
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.common.money import Money

from .alerts import raise_alert
from .models import LargeTransactionReport, Severity

logger = logging.getLogger("sokopay.aml")

DEFAULT_RULES = {
    "large_txn_minor": 50_000_00,            # FIC large/cash transaction reporting [VERIFY]
    "velocity_out_count": 10,
    "velocity_window_min": 60,
    "structuring_threshold_minor": 10_000_00,
    "structuring_band": 0.8,                 # amounts in [80%, 100%) of the threshold
    "structuring_count": 3,
    "pass_through_min_inflow_minor": 2_000_00,
    "pass_through_ratio": 0.8,
    "pass_through_window_min": 120,
    "many_senders_count": 10,
    "new_account_days": 7,
    "new_account_out_minor": 5_000_00,
    "dormant_days": 90,
    "dormant_out_minor": 2_000_00,
    "new_device_hours": 24,
    "new_device_out_minor": 2_000_00,
    "agent_cycle_minor": 1_000_00,
    "agent_split_count": 3,
    "merchant_spike_factor": 5,
    "merchant_spike_min_minor": 10_000_00,
}


def rule(name: str):
    return {**DEFAULT_RULES, **getattr(settings, "AML_RULES", {})}[name]


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


def _label(user) -> str:
    name = (user.full_name or "").strip() or "Customer"
    return f"{name} · {user.phone[:6]}•••{user.phone[-4:]}"


def _is_refund(entry) -> bool:
    """Refunds/reversals put money back; they aren't customer activity."""
    return "refund" in (entry.narrative or "").lower() or "reversed" in (entry.narrative or "").lower()


# --- entry point -------------------------------------------------------------------
def on_ledger_entry(entry_id) -> None:
    """Ledger post-commit hook: queue the evaluation (inline in dev/tests)."""
    from .tasks import evaluate_entry
    evaluate_entry.delay(str(entry_id))


def evaluate_entry(entry_id) -> int:
    """Run the real-time rules for one committed entry. Returns how many rules fired."""
    from apps.ledger.models import JournalEntry
    entry = JournalEntry.objects.filter(pk=entry_id).prefetch_related("postings__account").first()
    if entry is None or _is_refund(entry):
        return 0
    fired = 0
    for p in entry.postings.all():
        code = p.account.code
        if code.startswith("customer_wallet:"):
            fired += _customer_rules(entry, p, code.split(":", 1)[1])
        elif code.startswith("agent_float:") and entry.reference_type in ("cash_in", "cash_out"):
            fired += _agent_rules(entry, p, code.split(":", 1)[1])
    return fired


# --- customer rules ------------------------------------------------------------------
def _customer_rules(entry, posting, user_id) -> int:
    from django.contrib.auth import get_user_model

    from apps.ledger.models import Posting
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return 0
    wallet_id = posting.account_id
    amount = abs(posting.amount)
    outgoing = posting.amount > 0           # debit on a liability wallet = money out
    now = posting.created_at
    flows = Posting.objects.filter(account_id=wallet_id).exclude(
        Q(entry__narrative__icontains="refund") | Q(entry__narrative__icontains="reversed"))
    fired = 0
    ev = {"entry": str(entry.id), "kind": entry.reference_type, "amount": _ghs(amount),
          "direction": "out" if outgoing else "in", "at": now.isoformat()}

    def alert(code, title, severity, extra=None):
        nonlocal fired
        fired += 1
        raise_alert(rule=code, title=title, severity=severity, subject_kind="customer",
                    subject_id=str(user.id), subject_label=_label(user), user=user,
                    evidence={**ev, **(extra or {})})

    # LARGE_TXN — a regulatory record, not an alert.
    if amount >= rule("large_txn_minor"):
        LargeTransactionReport.objects.get_or_create(
            entry_id=f"{entry.id}:{user.id}",
            defaults={"user": user, "kind": entry.reference_type or "other",
                      "direction": "out" if outgoing else "in", "amount_minor": amount, "occurred_at": now})

    # STRUCTURING — same direction, just under the threshold, repeatedly.
    t = rule("structuring_threshold_minor")
    lo = int(t * rule("structuring_band"))
    if lo <= amount < t:
        sign = Q(amount__gt=0) if outgoing else Q(amount__lt=0)
        near = flows.filter(sign, created_at__gte=now - timedelta(hours=24)).filter(
            Q(amount__gte=lo, amount__lt=t) | Q(amount__lte=-lo, amount__gt=-t)).count()
        if near >= rule("structuring_count"):
            alert("STRUCTURING", "Repeated amounts just under a threshold", Severity.MEDIUM,
                  {"count_24h": near, "threshold": _ghs(t)})

    if outgoing:
        # VELOCITY_OUT
        window = now - timedelta(minutes=rule("velocity_window_min"))
        n_out = flows.filter(amount__gt=0, created_at__gte=window).count()
        if n_out >= rule("velocity_out_count"):
            alert("VELOCITY_OUT", "Unusually many payments out in a short time", Severity.MEDIUM,
                  {"count": n_out, "window_min": rule("velocity_window_min")})

        # PASS_THROUGH — in, then nearly all straight out, wallet left near-empty.
        w = now - timedelta(minutes=rule("pass_through_window_min"))
        inflow = -int(flows.filter(amount__lt=0, created_at__gte=w).aggregate(s=Sum("amount"))["s"] or 0)
        outflow = int(flows.filter(amount__gt=0, created_at__gte=w).aggregate(s=Sum("amount"))["s"] or 0)
        from apps.ledger.services import natural_balance_of
        balance = natural_balance_of(posting.account)
        if (inflow >= rule("pass_through_min_inflow_minor") and outflow >= inflow * rule("pass_through_ratio")
                and balance < inflow * 0.1):
            alert("PASS_THROUGH", "Money received and sent straight on (possible mule)", Severity.HIGH,
                  {"inflow": _ghs(inflow), "outflow": _ghs(outflow), "balance_after": _ghs(balance)})

        day_out = int(flows.filter(amount__gt=0, created_at__gte=now - timedelta(hours=24))
                      .aggregate(s=Sum("amount"))["s"] or 0)

        # NEW_ACCOUNT_HIGH_OUT
        if (user.created_at >= now - timedelta(days=rule("new_account_days"))
                and day_out >= rule("new_account_out_minor")):
            alert("NEW_ACCOUNT_HIGH_OUT", "Large outflows from a new account", Severity.MEDIUM,
                  {"out_24h": _ghs(day_out), "account_age_days": (now - user.created_at).days})

        # DORMANT_REACTIVATION — silent for N days (but older than that), then a big send.
        dormant = rule("dormant_days")
        if (amount >= rule("dormant_out_minor") and user.created_at < now - timedelta(days=dormant)
                and not flows.filter(created_at__gte=now - timedelta(days=dormant), created_at__lt=now)
                .exclude(pk=posting.pk).exists()):
            alert("DORMANT_REACTIVATION", "Dormant account suddenly active", Severity.MEDIUM,
                  {"dormant_days": dormant})

        # NEW_DEVICE_HIGH_OUT — new phone, established account, big outflow: takeover?
        from apps.notifications.models import Device
        hours = rule("new_device_hours")
        new_device = Device.objects.filter(user=user, created_at__gte=now - timedelta(hours=hours)).exists()
        if (new_device and day_out >= rule("new_device_out_minor")
                and user.created_at < now - timedelta(days=7)):
            alert("NEW_DEVICE_HIGH_OUT", "Large outflow soon after signing in on a new phone",
                  Severity.MEDIUM, {"out_24h": _ghs(day_out)})
    elif entry.reference_type == "p2p":
        # MANY_SENDERS — distinct SokoPay senders into this wallet in 24h.
        p2p_in = flows.filter(amount__lt=0, entry__reference_type="p2p",
                              created_at__gte=now - timedelta(hours=24)).values_list("entry_id", flat=True)
        senders = (Posting.objects.filter(entry_id__in=list(p2p_in), amount__gt=0,
                                          account__code__startswith="customer_wallet:")
                   .exclude(account_id=wallet_id).values("account_id").distinct().count())
        if senders >= rule("many_senders_count"):
            alert("MANY_SENDERS", "Many different people sending to one wallet", Severity.HIGH,
                  {"distinct_senders_24h": senders})
    return fired


# --- agent rules ----------------------------------------------------------------------
def _agent_rules(entry, posting, agent_id) -> int:
    from apps.agents.models import Agent
    from apps.ledger.models import JournalEntry, Posting
    agent = Agent.objects.filter(pk=agent_id).select_related("user").first()
    if agent is None:
        return 0
    customer_posting = next((p for p in entry.postings.all()
                             if p.account.code.startswith("customer_wallet:")), None)
    if customer_posting is None:
        return 0
    now = posting.created_at
    since = now - timedelta(hours=24)
    # All of this agent's cash entries for this same customer in the last 24h.
    entries = JournalEntry.objects.filter(
        reference_type__in=("cash_in", "cash_out"), reference_id=str(agent.id), created_at__gte=since,
        postings__account_id=customer_posting.account_id).distinct()
    kinds = dict(entries.values_list("reference_type").annotate(n=Count("id")))
    totals = int(Posting.objects.filter(entry__in=entries, account_id=customer_posting.account_id)
                 .aggregate(s=Sum("amount"))["s"] or 0)
    gross = int(sum(abs(a) for a in Posting.objects.filter(entry__in=entries, account_id=customer_posting.account_id)
                    .values_list("amount", flat=True)))
    fired = 0
    label = f"{agent.display_name} (agent)"
    customer_code = customer_posting.account.code.split(":", 1)[1]
    ev = {"entry": str(entry.id), "customer": customer_code, "at": now.isoformat()}
    if kinds.get("cash_in") and kinds.get("cash_out") and gross >= rule("agent_cycle_minor"):
        fired += 1
        raise_alert(rule="AGENT_CASH_CYCLING", title="Cash-in and cash-out for the same customer, same day",
                    severity=Severity.MEDIUM, subject_kind="agent", subject_id=str(agent.id),
                    subject_label=label, user=None,
                    evidence={**ev, "cash_ins": kinds["cash_in"], "cash_outs": kinds["cash_out"],
                              "gross": _ghs(gross), "net": _ghs(abs(totals))})
    if kinds.get("cash_out", 0) >= rule("agent_split_count"):
        fired += 1
        raise_alert(rule="AGENT_SPLIT_CASHOUT", title="One customer's cash-out split into several",
                    severity=Severity.MEDIUM, subject_kind="agent", subject_id=str(agent.id),
                    subject_label=label, user=None, evidence={**ev, "cash_outs_24h": kinds["cash_out"]})
    return fired


# --- daily batch --------------------------------------------------------------------------
def run_daily(day=None) -> dict:
    """Patterns that need a whole day of data (scheduled 06:30)."""
    from apps.payments.models import Payment
    day = day or (timezone.localdate() - timedelta(days=1))
    start = timezone.make_aware(dt.datetime.combine(day, dt.time.min))
    end = start + timedelta(days=1)
    base = Payment.objects.filter(status="succeeded", mode="live", merchant__isnull=False)
    today = dict(base.filter(created_at__gte=start, created_at__lt=end)
                 .values_list("merchant_id").annotate(s=Sum("amount_minor")))
    fired = 0
    for merchant_id, total in today.items():
        prior = int(base.filter(merchant_id=merchant_id, created_at__gte=start - timedelta(days=30),
                                created_at__lt=start).aggregate(s=Sum("amount_minor"))["s"] or 0)
        avg = prior / 30
        if total >= rule("merchant_spike_min_minor") and total >= rule("merchant_spike_factor") * max(avg, 1):
            from apps.merchants.models import Merchant
            m = Merchant.objects.filter(pk=merchant_id).first()
            if m is None:
                continue
            fired += 1
            raise_alert(rule="MERCHANT_SPIKE", title="Merchant collections far above normal",
                        severity=Severity.MEDIUM, subject_kind="merchant", subject_id=str(m.id),
                        subject_label=m.trading_name or m.legal_name, user=None,
                        evidence={"day": day.isoformat(), "collected": _ghs(total),
                                  "daily_avg_30d": _ghs(int(avg))})
    return {"day": day.isoformat(), "merchant_spikes": fired}
