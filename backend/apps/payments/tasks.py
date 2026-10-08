"""
Poll pending payments.

A payment can get stuck in PENDING if a rail callback is lost. This job re-queries the
rail for the authoritative status of payments that have been pending for a while and
resolves them — the safety net behind the webhook path.
"""

from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.utils import timezone

from apps.rails.registry import get_rail
from apps.rails.types import RailStatus

from . import services
from .models import Payment

logger = logging.getLogger("sokopay.payments")


def poll(older_than_minutes: int = 3, give_up_after_hours: int = 24) -> dict:
    now = timezone.now()
    cutoff = now - dt.timedelta(minutes=older_than_minutes)
    floor = now - dt.timedelta(hours=give_up_after_hours)

    pending = Payment.objects.filter(
        status=Payment.Status.PENDING, created_at__lte=cutoff, created_at__gte=floor,
    ).exclude(rail_ref="")

    resolved = 0
    for payment in pending.iterator():
        rail = get_rail(payment.rail)
        status = rail.get_status(payment.rail_ref).status
        if status == RailStatus.SUCCEEDED:
            services._on_collection_success(payment)
            resolved += 1
        elif status == RailStatus.FAILED:
            services.fail_payment(payment, "rail_declined_poll")
            resolved += 1
        # else still pending/unknown — leave for the next run
    return {"checked": pending.count(), "resolved": resolved}


@shared_task(name="apps.payments.tasks.poll_pending_payments")
def poll_pending_payments() -> dict:
    return poll()
