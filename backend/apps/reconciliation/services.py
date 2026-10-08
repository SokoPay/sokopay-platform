"""
Reconciliation orchestration: gather our records and the rail's, classify, and persist
a ReconRun with a ReconItem per break.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction

from apps.payments.models import Payment

from .engine import classify
from .models import ReconItem, ReconRun

logger = logging.getLogger("sokopay.recon")


def our_records(rail: str, date: dt.date) -> dict[str, int]:
    """
    Our view of settled money for a rail on a date: succeeded payments keyed by the
    rail's reference, valued at the amount collected.
    """
    rows = Payment.objects.filter(
        rail=rail, status=Payment.Status.SUCCEEDED, completed_at__date=date,
    ).exclude(rail_ref="")
    return {p.rail_ref: p.total_minor for p in rows}


@transaction.atomic
def reconcile(rail: str, date: dt.date, ours: dict[str, int],
              theirs: dict[str, int]) -> ReconRun:
    """Classify ours vs theirs and persist the run and its breaks."""
    result = classify(ours, theirs)

    run = ReconRun.objects.create(
        rail=rail, date=date,
        matched_count=len(result.matched),
        mismatch_count=len(result.amount_mismatch),
        ours_only_count=len(result.ours_only),
        theirs_only_count=len(result.theirs_only),
    )

    items = []
    for ref, o, t in result.amount_mismatch:
        items.append(ReconItem(run=run, kind=ReconItem.Kind.MISMATCH, rail_ref=ref,
                               ours_minor=o, theirs_minor=t))
    for ref, o in result.ours_only:
        items.append(ReconItem(run=run, kind=ReconItem.Kind.OURS_ONLY, rail_ref=ref,
                               ours_minor=o))
    for ref, t in result.theirs_only:
        items.append(ReconItem(run=run, kind=ReconItem.Kind.THEIRS_ONLY, rail_ref=ref,
                               theirs_minor=t))
    ReconItem.objects.bulk_create(items)

    if not run.is_clean:
        logger.warning("Recon %s %s found %d break(s)", rail, date, run.break_count)
    return run
