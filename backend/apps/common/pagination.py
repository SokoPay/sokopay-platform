"""
Keyset ("cursor") pagination for newest-first histories in the mobile APIs.

Why not page numbers: while someone scrolls, new transactions keep arriving; with
OFFSET paging every new row shifts later pages and items repeat or vanish. A keyset
cursor ("everything older than this row") is stable, and stays fast on large tables
because it rides the (owner, created_at) index.

The cursor is opaque to clients: base64 of "<created_at ISO>|<uuid id>". Ties on
created_at are broken by id, so no row is ever skipped.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid

from django.db.models import Q, QuerySet
from django.utils import timezone

PERIODS = {"today": 0, "7d": 7, "30d": 30}


class BadCursor(ValueError):
    pass


def encode_cursor(obj) -> str:
    return base64.urlsafe_b64encode(f"{obj.created_at.isoformat()}|{obj.id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        ts, pk = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return dt.datetime.fromisoformat(ts), uuid.UUID(pk)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise BadCursor("Invalid cursor.") from exc


def period_start(period: str) -> dt.datetime:
    """Start of today (Ghana time) minus the period's days. Raises KeyError if unknown."""
    days = PERIODS[period]
    start_today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return start_today - dt.timedelta(days=days)


def page(qs: QuerySet, cursor: str | None, size: int) -> tuple[list, str | None]:
    """One newest-first page after `cursor`. Returns (rows, next_cursor or None)."""
    if cursor:
        ts, pk = decode_cursor(cursor)
        qs = qs.filter(Q(created_at__lt=ts) | Q(created_at=ts, id__lt=pk))
    rows = list(qs.order_by("-created_at", "-id")[: size + 1])
    more = len(rows) > size
    rows = rows[:size]
    return rows, (encode_cursor(rows[-1]) if more else None)


def mask_phone(phone: str) -> str:
    """+233244058519 → +23324•••8519 (enough to recognise, not to reuse)."""
    return f"{phone[:6]}•••{phone[-4:]}" if len(phone or "") >= 7 else "•••"
