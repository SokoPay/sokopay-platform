"""Scheduled reconciliation task."""

from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.utils import timezone

from apps.rails.registry import get_rail

from . import services

logger = logging.getLogger("sokopay.recon")


@shared_task(name="apps.reconciliation.tasks.run_daily_reconciliation")
def run_daily_reconciliation(rail: str | None = None, date: str | None = None) -> dict:
    """
    Reconcile a rail for a date (defaults: configured rail, yesterday).

    "Theirs" comes from the rail's settlement report. A real rail implements
    `settled_transactions(date) -> {rail_ref: amount_minor}`; the mock/stubs don't yet,
    so until a partner is integrated the rail side is treated as empty and every one of
    our records surfaces as an `ours_only` break for ops to review. [VERIFY on integration]
    """
    from django.conf import settings
    rail = rail or getattr(settings, "RAIL_PROVIDER", "mock")
    day = dt.date.fromisoformat(date) if date else (timezone.now().date() - dt.timedelta(days=1))

    ours = services.our_records(rail, day)

    provider = get_rail(rail)
    fetch = getattr(provider, "settled_transactions", None)
    theirs = fetch(day) if callable(fetch) else {}

    run = services.reconcile(rail, day, ours, theirs)
    return {
        "rail": rail, "date": str(day),
        "matched": run.matched_count, "breaks": run.break_count,
    }
