"""
Account statements straight from the ledger (the books are the source of truth, so a
statement always agrees with the balance).

  customer_statement(user, start, end)       the SokoPay wallet (DEMI)
  merchant_statement(merchant, start, end)   the merchant's SokoPay balance (takings,
                                             fees already netted, refunds, settlements)

Each statement: opening balance, every posting in the period (date, details, reference,
money in, money out, running balance), closing balance. Periods are local dates
(Africa/Accra) and at most MAX_DAYS long. Output as JSON-ready dict, CSV or PDF.

Download links for the apps: a signed, single-use token valid for LINK_TTL seconds, so
a phone can open the PDF in the browser without putting its login token in a URL.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import secrets

from django.core import signing
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from apps.common.money import Money
from apps.common.pdf import SimplePdf

from .models import LedgerAccount, Posting

MAX_DAYS = 366
LINK_TTL = 300
_SALT = "sokopay.statement"
_UUID = re.compile(r"\s*\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


class StatementError(ValueError):
    pass


def parse_period(start: str | None, end: str | None) -> tuple[dt.date, dt.date]:
    today = timezone.localdate()
    try:
        s = dt.date.fromisoformat(start) if start else today.replace(day=1)
        e = dt.date.fromisoformat(end) if end else today
    except ValueError:
        raise StatementError("Dates must look like 2026-10-01.") from None
    if s > e:
        raise StatementError("The start date is after the end date.")
    if e > today:
        e = today
    if (e - s).days >= MAX_DAYS:
        raise StatementError(f"A statement can cover at most {MAX_DAYS} days.")
    return s, e


def _bounds(s: dt.date, e: dt.date):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(dt.datetime.combine(s, dt.time.min), tz),
            timezone.make_aware(dt.datetime.combine(e + dt.timedelta(days=1), dt.time.min), tz))


def _details(narrative: str) -> str:
    """Internal ids don't belong on a customer document."""
    return _UUID.sub("", narrative or "").strip() or "Transaction"


def account_statement(account: LedgerAccount | None, s: dt.date, e: dt.date) -> dict:
    lo, hi = _bounds(s, e)
    sign = account.normal_sign if account else 1
    rows, opening = [], 0
    if account is not None:
        opening = int(Posting.objects.filter(account=account, created_at__lt=lo)
                      .aggregate(t=Sum("amount"))["t"] or 0) * sign
        bal = opening
        for p in (Posting.objects.filter(account=account, created_at__gte=lo, created_at__lt=hi)
                  .select_related("entry").order_by("created_at", "id").iterator()):
            amt = int(p.amount) * sign
            bal += amt
            rows.append({
                "date": timezone.localtime(p.created_at).strftime("%Y-%m-%d %H:%M"),
                "details": _details(p.entry.narrative),
                "reference": p.entry.reference_id if p.entry.reference_type == "payment" else "",
                "in_minor": amt if amt > 0 else 0, "out_minor": -amt if amt < 0 else 0, "balance_minor": bal,
            })
    closing = rows[-1]["balance_minor"] if rows else opening
    return {"from": s.isoformat(), "to": e.isoformat(), "opening_minor": opening, "closing_minor": closing,
            "total_in_minor": sum(r["in_minor"] for r in rows),
            "total_out_minor": sum(r["out_minor"] for r in rows), "rows": rows}


def _payment_refs(st: dict) -> dict:
    """Swap payment ids for their SP- references (what the customer recognises)."""
    from apps.payments.models import Payment
    ids = {r["reference"] for r in st["rows"] if r["reference"]}
    refs = dict(Payment.objects.filter(id__in=ids).values_list("id", "reference")) if ids else {}
    refs = {str(k): v for k, v in refs.items()}
    for r in st["rows"]:
        r["reference"] = refs.get(r["reference"], "")
    return st


def customer_statement(user, s: dt.date, e: dt.date) -> dict:
    acc = LedgerAccount.objects.filter(code=f"customer_wallet:{user.id}").first()   # no wallet yet: empty
    st = _payment_refs(account_statement(acc, s, e))
    from apps.wallet.accounts_lookup import format_wallet_number, wallet_number_for
    st["holder"] = {"name": user.full_name or "", "phone": user.phone,
                    "account": f"SokoPay wallet {format_wallet_number(wallet_number_for(user))}"}
    return st


def merchant_statement(merchant, s: dt.date, e: dt.date) -> dict:
    acc = LedgerAccount.objects.filter(code=f"merchant_payable:{merchant.id}").first()
    st = _payment_refs(account_statement(acc, s, e))
    st["holder"] = {"name": merchant.trading_name or merchant.legal_name, "phone": "",
                    "account": f"SokoPay merchant balance · {merchant.short_code or ''}".strip(" ·")}
    return st


# --- rendering ------------------------------------------------------------------------------
def _m(minor: int) -> str:
    return Money(int(minor), "GHS").format()


def _plain(minor: int) -> str:
    """For CSV/PDF: 'GHS 1,234.50' (the cedi sign isn't in the PDF fonts)."""
    return f"GHS {minor / 100:,.2f}" if minor >= 0 else f"-GHS {-minor / 100:,.2f}"


def to_json(st: dict) -> dict:
    out = dict(st)
    out.update(opening_display=_m(st["opening_minor"]), closing_display=_m(st["closing_minor"]),
               total_in_display=_m(st["total_in_minor"]), total_out_display=_m(st["total_out_minor"]))
    return out


def to_csv(st: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["SokoPay statement", _csv_safe(st["holder"]["name"]), _csv_safe(st["holder"]["account"])])
    w.writerow(["Period", st["from"], st["to"]])
    w.writerow(["Opening balance (GHS)", f"{st['opening_minor'] / 100:.2f}"])
    w.writerow([])
    w.writerow(["Date", "Details", "Reference", "Money in (GHS)", "Money out (GHS)", "Balance (GHS)"])
    for r in st["rows"]:
        w.writerow([r["date"], _csv_safe(r["details"]), r["reference"],
                    f"{r['in_minor'] / 100:.2f}" if r["in_minor"] else "",
                    f"{r['out_minor'] / 100:.2f}" if r["out_minor"] else "", f"{r['balance_minor'] / 100:.2f}"])
    w.writerow([])
    w.writerow(["Closing balance (GHS)", f"{st['closing_minor'] / 100:.2f}"])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM so Excel reads UTF-8


def _csv_safe(v: str) -> str:
    """Stop spreadsheet formula injection from merchant-controlled text."""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def to_pdf(st: dict) -> bytes:
    h = st["holder"]
    doc = SimplePdf(title=f"SokoPay statement {st['from']} to {st['to']}",
                    footer=f"SokoPay statement · {h['account']} · generated "
                           f"{timezone.localtime().strftime('%Y-%m-%d %H:%M')}")
    doc.text("SokoPay", size=18, bold=True)
    doc.text("Account statement", size=12)
    doc.text(h["name"], size=10, bold=True, gap=6)
    if h["phone"]:
        doc.text(h["phone"])
    doc.text(h["account"])
    doc.text(f"Period: {st['from']} to {st['to']}", gap=4)
    doc.text(f"Opening balance: {_plain(st['opening_minor'])}     Money in: {_plain(st['total_in_minor'])}     "
             f"Money out: {_plain(st['total_out_minor'])}     Closing balance: {_plain(st['closing_minor'])}",
             bold=True, gap=4)
    widths = [78, 190, 78, 56, 56, 57]
    doc.row(["Date", "Details", "Reference", "In", "Out", "Balance"], widths, bold=True, right={3, 4, 5})
    doc.rule()
    for r in st["rows"]:
        doc.row([r["date"], r["details"], r["reference"],
                 f"{r['in_minor'] / 100:,.2f}" if r["in_minor"] else "",
                 f"{r['out_minor'] / 100:,.2f}" if r["out_minor"] else "",
                 f"{r['balance_minor'] / 100:,.2f}"], widths, right={3, 4, 5})
    if not st["rows"]:
        doc.text("No transactions in this period.", gap=6)
    doc.rule()
    doc.text(f"Closing balance: {_plain(st['closing_minor'])}", bold=True)
    doc.text("Amounts in Ghana cedis. Questions? Contact SokoPay support with the reference.", size=8, gap=8)
    return doc.render()


# --- single-use download links ----------------------------------------------------------------
def make_link_token(*, kind: str, owner_id: str, start: dt.date, end: dt.date, fmt: str) -> str:
    return signing.TimestampSigner(salt=_SALT).sign_object(
        {"k": kind, "o": owner_id, "s": start.isoformat(), "e": end.isoformat(), "f": fmt,
         "n": secrets.token_urlsafe(12)})


def redeem_link_token(token: str) -> dict:
    try:
        data = signing.TimestampSigner(salt=_SALT).unsign_object(token, max_age=LINK_TTL)
    except signing.SignatureExpired:
        raise StatementError("This download link has expired. Request a new one.") from None
    except signing.BadSignature:
        raise StatementError("Invalid download link.") from None
    if not cache.add(f"stmt-link:{data['n']}", 1, timeout=LINK_TTL + 60):
        raise StatementError("This download link has already been used.")
    return data
