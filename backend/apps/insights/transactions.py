"""
Platform-wide transaction monitoring: one feed over every kind of money movement, with
one status vocabulary, for the back office.

Each source is the system of record for its kind of event (no double counting):

  payment       Payment (live mode): bills, airtime, data, merchant payments, wallet top-ups
  transfer      ExternalTransfer: wallet → MoMo / bank / other wallets
  cross_border  CrossBorderTransfer
  lifestyle     LifestyleOrder: tickets, food
  remittance    InboundRemittance: money in from abroad
  agent         AgentTxn: completed cash-in, cash-out and float top-ups
  cashout_req   CashOutRequest that did NOT complete (approved ones are AgentTxn rows)
  float_req     FloatTopUp still pending or rejected (approved ones are AgentTxn rows)
  settlement    Settlement: merchant payouts
  refund        Refund
  bulk          BulkPayoutItem once sent (processing / paid / failed)
  product       ProductTransaction: premiums, loan disbursements, savings
  p2p           Ledger entries between SokoPay wallets (they have no row of their own)

Normalised statuses (the usual payments lifecycle):
  pending     created, waiting for the customer, an approver or the partner
  processing  sent to the partner, waiting for its final answer
  completed   money moved
  failed      the partner or the system refused it (money returned where applicable)
  cancelled   declined, expired, rejected by a reviewer, or cancelled
  reversed    completed, then refunded

Privacy: phone numbers are masked and ledger narratives never shown. Amounts are integer
pesewas throughout.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from django.apps import apps
from django.db.models import Count, Q, Sum
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.common.pagination import mask_phone

STATUSES = ("pending", "processing", "completed", "failed", "cancelled", "reversed")
STATUS_LABEL = {"pending": "Pending", "processing": "Processing", "completed": "Completed",
                "failed": "Failed", "cancelled": "Cancelled", "reversed": "Reversed"}
_NORMALISE = {
    "created": "pending", "pending": "pending", "awaiting_approval": "pending", "requested": "pending",
    "open": "pending", "processing": "processing",
    "succeeded": "completed", "paid": "completed", "approved": "completed", "credited": "completed",
    "completed": "completed",
    "failed": "failed",
    "declined": "cancelled", "expired": "cancelled", "cancelled": "cancelled", "rejected": "cancelled",
    "refunded": "reversed",
}
MAX_ROWS = 200


def normalise(raw: str) -> str:
    return _NORMALISE.get(raw, "pending")


def raw_statuses(norm: str, choices) -> list[str]:
    return [value for value in choices if normalise(value) == norm]


@dataclass
class Source:
    key: str
    model: str                       # "app.Model"
    label: str                       # type shown in the list
    amount: str = "amount_minor"
    fee: str | None = None
    status: str | None = "status"    # None = always completed
    reference: str | None = None
    base: Q = field(default_factory=Q)
    exclude: Q | None = None
    ref_type: str = ""               # ledger reference_type, to match compliance alerts
    type_field: str | None = None    # a field whose display value refines the label

    def qs(self):
        model = apps.get_model(self.model)
        qs = model.objects.filter(self.base)
        if self.exclude is not None:
            qs = qs.exclude(self.exclude)
        return qs

    def status_values(self) -> list[str]:
        if self.status is None:
            return ["completed"]
        return [c[0] for c in apps.get_model(self.model)._meta.get_field(self.status).choices]


SOURCES: list[Source] = [
    Source("payment", "payments.Payment", "Payment", fee="fee_minor", reference="reference",
           base=Q(mode="live"), ref_type="payment", type_field="purpose"),
    Source("transfer", "wallet.ExternalTransfer", "Transfer out", reference="reference", ref_type="transfer"),
    Source("cross_border", "wallet.CrossBorderTransfer", "Cross-border send", fee="fee_minor",
           reference="reference", ref_type="cross_border"),
    Source("lifestyle", "wallet.LifestyleOrder", "Tickets & food", amount="total_minor",
           reference="reference", ref_type="lifestyle_order"),
    Source("remittance", "wallet.InboundRemittance", "Remittance in", reference="partner_ref",
           ref_type="remittance"),
    Source("agent", "agents.AgentTxn", "Agent", status=None, type_field="kind", ref_type="agent_txn"),
    Source("cashout_req", "agents.CashOutRequest", "Cash-out request", fee="fee_minor",
           exclude=Q(status="approved")),
    Source("float_req", "agents.FloatTopUp", "Float top-up request", reference="payment_reference",
           exclude=Q(status="approved")),
    Source("settlement", "merchants.Settlement", "Merchant settlement", reference="rail_ref",
           ref_type="settlement"),
    Source("refund", "merchants.Refund", "Refund", reference="rail_ref", ref_type="refund"),
    Source("bulk", "bulk.BulkPayoutItem", "Bulk payout", fee="fee_minor", reference="provider_ref",
           base=Q(status__in=("processing", "paid", "failed")), ref_type="bulk_payout_item"),
    Source("product", "marketplace.ProductTransaction", "Financial product", reference="partner_ref",
           type_field="kind"),
]
TYPES = {s.key: s.label for s in SOURCES} | {"p2p": "Wallet to wallet"}


def day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = timezone.make_aware(dt.datetime.combine(day, dt.time.min))
    return start, start + dt.timedelta(days=1)


# --- compliance flags ----------------------------------------------------------------------
def flagged_refs() -> set[tuple[str, str]]:
    """(ledger reference_type, reference_id) of every entry cited by an open AML alert."""
    from apps.compliance.models import Alert
    from apps.ledger.models import JournalEntry
    entry_ids = set()
    for evidence in Alert.objects.filter(status__in=("open", "investigating", "escalated")) \
            .values_list("evidence", flat=True)[:2000]:
        for item in evidence or []:
            if isinstance(item, dict) and item.get("entry"):
                entry_ids.add(str(item["entry"]))
    if not entry_ids:
        return set()
    entries = list(JournalEntry.objects.filter(pk__in=entry_ids))
    refs = {(e.reference_type, e.reference_id) for e in entries} | {("p2p_entry", str(e.pk)) for e in entries}
    # Agent ledger entries reference the agent, not the AgentTxn row: match by agent, kind and time.
    from apps.agents.models import AgentTxn
    kinds = {"cash_in": "cash_in", "cash_out": "cash_out", "agent_topup": "topup"}
    for e in entries:
        if e.reference_type in kinds:
            near = (AgentTxn.objects.filter(agent_id=e.reference_id, kind=kinds[e.reference_type],
                                            created_at__gte=e.created_at - dt.timedelta(seconds=5),
                                            created_at__lte=e.created_at + dt.timedelta(seconds=5))
                    .order_by("created_at").first())
            if near:
                refs.add(("agent_txn", str(near.pk)))
    return refs


# --- rows ----------------------------------------------------------------------------------
def _person_of(obj):
    application = getattr(obj, "application", None)          # ProductTransaction → its application's user
    return getattr(application, "user", None) if application is not None else None


def _party(obj) -> str:
    """Who it concerns, masked: never a full phone number in a list."""
    applicant = _person_of(obj)
    if applicant is not None:
        return f"{(applicant.full_name or '').split(' ')[0]} {mask_phone(applicant.phone)}".strip()
    for attr in ("user", "sender", "customer", "recipient", "recipient_user"):
        person = getattr(obj, attr, None)
        if person is not None and hasattr(person, "phone"):
            name = (person.full_name or "").split(" ")[0]
            return f"{name} {mask_phone(person.phone)}".strip()
    for attr in ("merchant",):
        m = getattr(obj, attr, None)
        if m is not None:
            return m.trading_name or m.legal_name
    agent = getattr(obj, "agent", None)
    if agent is not None:
        return f"Agent: {agent.display_name}"
    batch = getattr(obj, "batch", None)
    if batch is not None:
        return batch.merchant.trading_name or batch.merchant.legal_name
    return ""


def _user_id(obj):
    applicant = _person_of(obj)
    if applicant is not None:
        return applicant.pk
    for attr in ("user", "sender", "customer", "recipient", "recipient_user"):
        person = getattr(obj, attr, None)
        if person is not None and hasattr(person, "phone"):
            return person.pk
    agent = getattr(obj, "agent", None)
    return agent.user_id if agent is not None else None


def _row(src: Source, obj, flagged: set) -> dict:
    raw = getattr(obj, src.status) if src.status else "completed"
    label = src.label
    if src.type_field:
        label = f"{label}: {getattr(obj, f'get_{src.type_field}_display')()}"
    status_label = getattr(obj, f"get_{src.status}_display")() if src.status else "Completed"
    ref = getattr(obj, src.reference, "") if src.reference else ""
    uid = _user_id(obj)
    try:
        user_url = reverse("portal:admin_user", args=[uid]) if uid else ""
    except NoReverseMatch:
        user_url = ""
    return {
        "source": src.key,
        "id": str(obj.pk),
        "type": label,
        "reference": ref or "",
        "amount_minor": int(getattr(obj, src.amount) or 0),
        "fee_minor": int(getattr(obj, src.fee) or 0) if src.fee else 0,
        "status": normalise(raw),
        "status_raw": status_label,
        "party": _party(obj),
        "user_url": user_url,
        "created_at": obj.created_at,
        "updated_at": getattr(obj, "completed_at", None) or obj.updated_at,
        "flagged": bool(src.ref_type) and (src.ref_type, str(obj.pk)) in flagged,
    }


def _p2p_qs(start, end):
    from apps.ledger.models import JournalEntry
    return JournalEntry.objects.filter(reference_type="p2p", created_at__gte=start, created_at__lt=end)


def _p2p_rows(start, end, flagged, before=None, limit=MAX_ROWS) -> list[dict]:
    from apps.ledger.models import Posting
    qs = _p2p_qs(start, end)
    if before:
        qs = qs.filter(created_at__lt=before)
    entries = list(qs.order_by("-created_at")[:limit])
    totals = dict(Posting.objects.filter(entry__in=entries, amount__gt=0).values("entry")
                  .annotate(t=Sum("amount")).values_list("entry", "t"))
    return [{
        "source": "p2p", "id": str(e.pk), "type": "Wallet to wallet", "reference": "",
        "amount_minor": int(totals.get(e.pk, 0)), "fee_minor": 0, "status": "completed",
        "status_raw": "Completed", "party": "", "user_url": "", "created_at": e.created_at,
        "updated_at": e.created_at, "flagged": ("p2p_entry", str(e.pk)) in flagged,
    } for e in entries]


def rows(*, day: dt.date, status: str = "", type_key: str = "", flagged_only: bool = False,
         search: str = "", before: dt.datetime | None = None, limit: int = 50) -> list[dict]:
    """Newest-first rows for one day, filtered. `before` pages older rows (keyset)."""
    start, end = day_bounds(day)
    flagged = flagged_refs()
    out: list[dict] = []
    search = search.strip()
    for src in SOURCES:
        if type_key and type_key != src.key:
            continue
        qs = src.qs().filter(created_at__gte=start, created_at__lt=end)
        if status:
            if src.status is None:
                if status != "completed":
                    continue
            else:
                qs = qs.filter(**{f"{src.status}__in": raw_statuses(status, src.status_values())})
        if search:
            if not src.reference:
                continue
            qs = qs.filter(**{f"{src.reference}__iexact": search})
        if before:
            qs = qs.filter(created_at__lt=before)
        if flagged_only:
            if not src.ref_type:
                continue
            ids = [rid for rtype, rid in flagged if rtype == src.ref_type]
            qs = qs.filter(pk__in=ids)
        related = [f.name for f in qs.model._meta.get_fields()
                   if f.is_relation and f.many_to_one and f.name in
                   ("user", "sender", "customer", "recipient", "recipient_user", "merchant", "agent", "batch",
                    "application")]
        out += [_row(src, obj, flagged) for obj in qs.select_related(*related).order_by("-created_at")[:limit]]
    if (not type_key or type_key == "p2p") and status in ("", "completed") and not search:
        p2p = _p2p_rows(start, end, flagged, before, limit)
        out += [r for r in p2p if r["flagged"]] if flagged_only else p2p
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out[:limit]


# --- aggregates ----------------------------------------------------------------------------
def day_summary(day: dt.date) -> dict:
    """Counts and values for one day: by normalised status and by type (pesewas)."""
    start, end = day_bounds(day)
    by_status = {s: {"count": 0, "value": 0} for s in STATUSES}
    by_type: dict[str, dict] = {}
    for src in SOURCES:
        qs = src.qs().filter(created_at__gte=start, created_at__lt=end)
        if src.status is None:
            agg = qs.aggregate(n=Count("id"), v=Sum(src.amount))
            groups = [("completed", agg["n"] or 0, agg["v"] or 0)]
        else:
            groups = [(normalise(g[src.status]), g["n"], g["v"] or 0)
                      for g in qs.values(src.status).annotate(n=Count("id"), v=Sum(src.amount))]
        for norm, n, v in groups:
            by_status[norm]["count"] += n
            by_status[norm]["value"] += int(v)
            t = by_type.setdefault(src.key, {"label": src.label, "count": 0, "completed_value": 0})
            t["count"] += n
            if norm == "completed":
                t["completed_value"] += int(v)
    from apps.ledger.models import Posting
    p2p_n = _p2p_qs(start, end).count()
    p2p_v = int(Posting.objects.filter(entry__in=_p2p_qs(start, end), amount__gt=0)
                .aggregate(v=Sum("amount"))["v"] or 0)
    by_status["completed"]["count"] += p2p_n
    by_status["completed"]["value"] += p2p_v
    if p2p_n:
        by_type["p2p"] = {"label": "Wallet to wallet", "count": p2p_n, "completed_value": p2p_v}
    total = sum(s["count"] for s in by_status.values())
    finished = by_status["completed"]["count"] + by_status["failed"]["count"]
    return {
        "day": day,
        "by_status": by_status,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1]["completed_value"])),
        "total_count": total,
        "completed_value": by_status["completed"]["value"],
        "failure_rate": round(100 * by_status["failed"]["count"] / finished, 1) if finished else 0.0,
    }


def daily_series(days: int = 14, end_day: dt.date | None = None) -> list[dict]:
    """
    Per day, oldest first: completed count and value, failed count, failure rate, and
    completed count per type (for charts and anomaly checks). One grouped query per source.
    """
    from django.db.models.functions import TruncDate

    from apps.ledger.models import Posting
    end_day = end_day or timezone.localdate()
    first = end_day - dt.timedelta(days=days - 1)
    start, _ = day_bounds(first)
    _, end = day_bounds(end_day)
    blank = lambda d: {"day": d, "count": 0, "value": 0, "failed": 0, "by_type": {}}  # noqa: E731
    series = {first + dt.timedelta(days=i): blank(first + dt.timedelta(days=i)) for i in range(days)}

    def add(day, key, norm, n, v):
        point = series.get(day)
        if point is None:
            return
        if norm == "completed":
            point["count"] += n
            point["value"] += int(v or 0)
            point["by_type"][key] = point["by_type"].get(key, 0) + n
        elif norm == "failed":
            point["failed"] += n

    for src in SOURCES:
        qs = src.qs().filter(created_at__gte=start, created_at__lt=end).annotate(d=TruncDate("created_at"))
        if src.status is None:
            for g in qs.values("d").annotate(n=Count("id"), v=Sum(src.amount)):
                add(g["d"], src.key, "completed", g["n"], g["v"])
        else:
            for g in qs.values("d", src.status).annotate(n=Count("id"), v=Sum(src.amount)):
                add(g["d"], src.key, normalise(g[src.status]), g["n"], g["v"])
    for g in (Posting.objects.filter(entry__reference_type="p2p", entry__created_at__gte=start,
                                     entry__created_at__lt=end, amount__gt=0)
              .annotate(d=TruncDate("entry__created_at")).values("d")
              .annotate(n=Count("entry", distinct=True), v=Sum("amount"))):
        add(g["d"], "p2p", "completed", g["n"], g["v"])
    out = []
    for point in series.values():
        finished = point["count"] + point["failed"]
        point["failure_rate"] = round(100 * point["failed"] / finished, 1) if finished else 0.0
        out.append(point)
    return out
