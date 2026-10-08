"""
A customer's activity: one newest-first feed over everything that moved their money.

Three sources, each the system of record for its kind of event:

  payment   Payment rows (user = me): bills, airtime, data, shops, wallet top-ups —
            whether paid by MoMo or from the wallet.
  transfer  ExternalTransfer rows (sender = me): to MoMo, banks, other fintech wallets.
  wallet    Ledger postings on my wallet for events with no row of their own: money
            from/to SokoPay users (p2p), agent cash-in/out, remittances, loans and
            premiums, salary/bulk credits.

No double counting: wallet postings tagged "payment" or "transfer" belong to the first
two sources and are excluded here (WALLET_ONLY_TYPES).

Paging: a keyset cursor over the merged stream, ordered by (created_at, source rank,
id) descending. Each source is asked for "rows older than the cursor" (with the
tie-break) and the results are merged — stable while new activity arrives.

Privacy: ledger narratives (which can contain phone numbers) are never exposed;
counterparties appear as "Kofi M." / agent shop names; external accounts are masked.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid
from dataclasses import dataclass

from django.db.models import Q, Sum

from apps.common import pagination
from apps.common.money import Money
from apps.common.pagination import mask_phone

PAGE_SIZE = 30
RANK = {"payment": 2, "transfer": 1, "wallet": 0}
WALLET_ONLY_TYPES = ("p2p", "cash_in", "cash_out", "remittance", "product_application",
                     "bulk_payout_item", "cross_border", "refund", "lifestyle_order", "savings")
FILTERS = {"payments", "transfers", "wallet"}
NETWORK_LABEL = {"mtn": "MTN MoMo", "telecel": "Telecel Cash", "at": "AT Money", "card": "Card",
                 "wallet": "SokoPay wallet"}


class BadCursor(ValueError):
    pass


@dataclass
class Item:
    created_at: dt.datetime
    source: str
    pk: object
    body: dict

    @property
    def key(self):
        return (self.created_at, RANK[self.source], str(self.pk))


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


# --- cursor ----------------------------------------------------------------------
def encode(item: Item) -> str:
    raw = f"{item.created_at.isoformat()}|{item.source}|{item.pk}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode(cursor: str):
    try:
        ts, source, pk = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 2)
        if source not in RANK:
            raise ValueError
        pk = int(pk) if source == "wallet" else uuid.UUID(pk)
        return dt.datetime.fromisoformat(ts), source, pk
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise BadCursor("Invalid cursor.") from exc


def _older_than(qs, source: str, cursor, ts_field="created_at", pk_field="id"):
    """Rows of `source` strictly after `cursor` in the merged descending order."""
    if cursor is None:
        return qs
    ts, c_source, c_pk = cursor
    older = Q(**{f"{ts_field}__lt": ts})
    if RANK[source] < RANK[c_source]:          # same instant, lower rank → comes after
        older |= Q(**{ts_field: ts})
    elif source == c_source:                   # same instant, same source → id tie-break
        older |= Q(**{ts_field: ts, f"{pk_field}__lt": c_pk})
    return qs.filter(older)


# --- sources ---------------------------------------------------------------------
def _payments(user, *, kind_filter, since, cursor, n):
    from apps.payments.models import Payment
    qs = Payment.objects.filter(user=user, mode="live").select_related("biller", "merchant")
    if kind_filter == "payments":
        qs = qs.exclude(purpose="wallet_fund")
    elif kind_filter == "wallet":
        qs = qs.filter(purpose="wallet_fund")
    elif kind_filter == "transfers":
        return []
    if since:
        qs = qs.filter(created_at__gte=since)
    rows = _older_than(qs, "payment", cursor).order_by("-created_at", "-id")[:n]
    out = []
    for p in rows:
        if p.purpose == "wallet_fund":
            title, category, direction, amount = "Wallet top-up", "topup", "in", p.amount_minor
        elif p.purpose == "merchant":
            shop = (p.merchant.trading_name or p.merchant.legal_name) if p.merchant else "Shop"
            title, category, direction, amount = shop, "shop", "out", p.total_minor
        elif p.purpose == "airtime":
            title, category, direction, amount = "Airtime", "airtime", "out", p.total_minor
        elif p.purpose == "data":
            title, category, direction, amount = "Data bundle", "data", "out", p.total_minor
        else:
            title = p.biller.name if p.biller_id else "Bill payment"
            category, direction, amount = "bill", "out", p.total_minor
        subtitle = NETWORK_LABEL.get(p.funding_source if p.funding_source == "wallet" else p.network,
                                     p.network.upper())
        if p.account_ref and p.purpose in ("bill", "airtime", "data"):
            subtitle += f" · {p.account_ref}"
        status = {"succeeded": "succeeded", "failed": "failed", "refunded": "refunded"}.get(p.status, "pending")
        out.append(Item(p.created_at, "payment", p.id, {
            "category": category, "title": title, "subtitle": subtitle,
            "direction": direction, "amount_minor": amount, "status": status,
            "reference": p.reference,
        }))
    return out


def _transfers(user, *, kind_filter, since, cursor, n):
    if kind_filter in ("payments", "wallet"):
        return []
    from apps.wallet.models import ExternalTransfer
    qs = ExternalTransfer.objects.filter(sender=user)
    if since:
        qs = qs.filter(created_at__gte=since)
    out = []
    for t in _older_than(qs, "transfer", cursor).order_by("-created_at", "-id")[:n]:
        who = t.account_name or (mask_phone(t.account) if t.destination_type != "bank"
                                 else f"•••{t.account[-4:]}")
        where = {"momo": NETWORK_LABEL.get(t.institution, t.institution.upper()),
                 "bank": f"Bank · {t.institution.upper()}",
                 "wallet": t.institution.title()}.get(t.destination_type, t.destination_type)
        out.append(Item(t.created_at, "transfer", t.id, {
            "category": "transfer", "title": f"To {who}", "subtitle": where,
            "direction": "out", "amount_minor": t.amount_minor,
            "status": {"succeeded": "succeeded", "failed": "refunded"}.get(t.status, "pending"),
            "reference": t.reference,
        }))
    return out


def _wallet(user, *, kind_filter, since, cursor, n):
    if kind_filter == "payments":
        return []
    from apps.ledger import accounts
    from apps.ledger.models import Posting
    types = WALLET_ONLY_TYPES
    if kind_filter == "transfers":
        types = ("p2p",)
    elif kind_filter == "wallet":
        types = tuple(t for t in WALLET_ONLY_TYPES if t != "p2p")
    wallet = accounts.customer_wallet(str(user.id))
    qs = Posting.objects.filter(account=wallet, entry__reference_type__in=types).select_related("entry")
    if since:
        qs = qs.filter(created_at__gte=since)
    rows = list(_older_than(qs, "wallet", cursor).order_by("-created_at", "-id")[:n])
    if not rows:
        return []
    names = _counterparties(rows, wallet.id)
    out = []
    for p in rows:
        ref_type, ref_id = p.entry.reference_type, p.entry.reference_id
        incoming = p.amount < 0                   # a credit on a liability wallet = money in
        other = names.get(p.entry_id, "")
        title, category = _wallet_title(ref_type, incoming, other, ref_id)
        out.append(Item(p.created_at, "wallet", p.id, {
            "category": category, "title": title,
            "subtitle": "SokoPay wallet",
            "direction": "in" if incoming else "out", "amount_minor": abs(p.amount),
            "status": "succeeded", "reference": "",
        }))
    return out


def _counterparties(rows, my_wallet_id) -> dict:
    """entry_id → a display name for the other side (another wallet or an agent)."""
    from apps.agents.models import Agent
    from apps.ledger.models import Posting
    from apps.wallet.services import display_name
    from django.contrib.auth import get_user_model

    others = (Posting.objects.filter(entry_id__in={p.entry_id for p in rows})
              .exclude(account_id=my_wallet_id).select_related("account"))
    owner = {}
    for o in others:
        code = o.account.code
        if code.startswith(("customer_wallet:", "agent_float:")):
            owner[o.entry_id] = code
    user_ids = {c.split(":", 1)[1] for c in owner.values() if c.startswith("customer_wallet:")}
    agent_ids = {c.split(":", 1)[1] for c in owner.values() if c.startswith("agent_float:")}
    users = {str(u.id): display_name(u) for u in get_user_model().objects.filter(id__in=user_ids)}
    agents = {str(a.id): a.display_name for a in Agent.objects.filter(id__in=agent_ids)}
    out = {}
    for entry_id, code in owner.items():
        kind, oid = code.split(":", 1)
        out[entry_id] = users.get(oid, "") if kind == "customer_wallet" else agents.get(oid, "")
    return out


def _wallet_title(ref_type: str, incoming: bool, other: str, ref_id: str) -> tuple[str, str]:
    if ref_type == "p2p":
        return ((f"From {other}" if other else "Received from a SokoPay user", "received") if incoming
                else (f"To {other}" if other else "Sent to a SokoPay user", "sent"))
    if ref_type == "cash_in":
        return (f"Cash deposit · {other}" if other else "Cash deposit", "cash_in")
    if ref_type == "cash_out":
        return (f"Cash withdrawal · {other}" if other else "Cash withdrawal", "cash_out")
    if ref_type == "remittance":
        from apps.wallet.models import InboundRemittance
        r = InboundRemittance.objects.filter(pk=ref_id).first()
        sender = r.sender_name if r and r.sender_name else "abroad"
        return (f"From {sender}", "remittance")
    if ref_type == "product_application":
        from apps.marketplace.models import ProductApplication
        a = ProductApplication.objects.filter(pk=ref_id).select_related("product").first()
        name = a.product.name if a else ""
        return ((f"Loan · {name}" if name else "Loan received", "loan") if incoming
                else (f"Premium · {name}" if name else "Insurance premium", "premium"))
    if ref_type == "bulk_payout_item":
        from apps.bulk.models import BulkPayoutItem
        it = BulkPayoutItem.objects.filter(pk=ref_id).select_related("batch__merchant").first()
        m = it.batch.merchant if it else None
        return (f"From {m.trading_name or m.legal_name}" if m else "Payment received", "salary")
    return ("Wallet", "wallet")


# --- feed --------------------------------------------------------------------------
def feed(user, *, kind_filter: str = "", period: str = "", cursor: str | None = None,
         size: int = PAGE_SIZE) -> dict:
    if kind_filter and kind_filter not in FILTERS:
        raise ValueError("Unknown filter.")
    since = pagination.period_start(period) if period else None    # KeyError if unknown
    decoded = decode(cursor) if cursor else None

    args = dict(kind_filter=kind_filter, since=since, cursor=decoded, n=size + 1)
    merged = (_payments(user, **args) + _transfers(user, **args) + _wallet(user, **args))
    merged.sort(key=lambda i: i.key, reverse=True)
    page, more = merged[:size], len(merged) > size

    results = []
    for it in page:
        b = it.body
        b.update(id=f"{it.source}:{it.pk}", source=it.source,
                 amount_display=("+ " if b["direction"] == "in" else "− ") + _ghs(b["amount_minor"]),
                 created_at=it.created_at.isoformat())
        results.append(b)
    return {
        "results": results,
        "next_cursor": encode(page[-1]) if more else None,
        "summary": None if cursor else summary(user, since),
    }


def summary(user, since) -> dict:
    """Money in / out for the period, counting only what actually completed."""
    from apps.ledger import accounts
    from apps.ledger.models import Posting
    from apps.payments.models import Payment
    from apps.wallet.models import ExternalTransfer

    def total(qs, field):
        if since:
            qs = qs.filter(created_at__gte=since)
        return int(qs.aggregate(t=Sum(field))["t"] or 0)

    pays = Payment.objects.filter(user=user, mode="live", status="succeeded")
    out_minor = total(pays.exclude(purpose="wallet_fund"), "total_minor")
    out_minor += total(ExternalTransfer.objects.filter(sender=user, status="succeeded"), "amount_minor")
    in_minor = total(pays.filter(purpose="wallet_fund"), "amount_minor")

    wallet = Posting.objects.filter(account=accounts.customer_wallet(str(user.id)),
                                    entry__reference_type__in=WALLET_ONLY_TYPES)
    out_minor += total(wallet.filter(amount__gt=0), "amount")
    in_minor += -total(wallet.filter(amount__lt=0), "amount")
    return {"money_in_display": _ghs(in_minor), "money_out_display": _ghs(out_minor)}
