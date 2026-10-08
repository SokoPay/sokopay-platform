"""
Health and operational metrics.

  /healthz   liveness (process up) for the load balancer
  /readyz    readiness: the database and cache answer (503 otherwise). No details leak.

ops_snapshot() counts the things that need a human, logged every 5 minutes as one JSON
line ({"metric": "ops", ...}). CloudWatch metric filters turn each field into a metric
and alarms page the on-call person (infra/monitoring.tf). The staff portal shows the
same numbers on the System health page.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger("sokopay.metrics")

SAFEGUARDING_CODE = {"ok": 0, "stale": 1, "no_data": 1, "shortfall": 2}


def readyz(_request):
    ok = True
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
    except Exception:   # noqa: BLE001
        ok = False
    try:
        cache.set("readyz", 1, 5)
        ok = ok and cache.get("readyz") == 1
    except Exception:   # noqa: BLE001
        ok = False
    return JsonResponse({"status": "ok" if ok else "unavailable"}, status=200 if ok else 503)


def ops_snapshot() -> dict:
    from apps.compliance.models import Alert
    from apps.marketplace.models import ProductTransaction
    from apps.merchants import refunds, settlement
    from apps.merchants.disputes import overdue
    from apps.merchants.models import WebhookDelivery
    from apps.payments.models import Payment
    from apps.reconciliation.models import ReconItem
    from apps.safeguarding.models import SafeguardingCheck
    from apps.wallet.models import CrossBorderTransfer, ExternalTransfer, LifestyleOrder

    now = timezone.now()
    hour_ago, quarter = now - timedelta(hours=1), now - timedelta(minutes=15)
    recent = Payment.objects.filter(created_at__gte=quarter, mode="live")
    latest = SafeguardingCheck.objects.order_by("-created_at").first()
    return {
        "stuck_settlements": settlement.stuck_settlements().count(),
        "stuck_refunds": refunds.stuck_refunds().count(),
        "transfers_pending_over_1h": ExternalTransfer.objects.filter(status="pending", created_at__lt=hour_ago).count(),
        "cross_border_pending_over_1h": CrossBorderTransfer.objects.filter(status="pending",
                                                                           created_at__lt=hour_ago).count(),
        "lifestyle_pending_over_1h": LifestyleOrder.objects.filter(status="pending", created_at__lt=hour_ago).count(),
        "payments_15m": recent.count(),
        "payments_failed_15m": recent.filter(status="failed").count(),
        "webhooks_abandoned_1h": WebhookDelivery.objects.filter(status="abandoned", updated_at__gte=hour_ago).count(),
        "aml_high_open": Alert.objects.filter(severity="high", status__in=("open", "investigating", "escalated")).count(),
        "recon_breaks_open": ReconItem.objects.filter(resolved=False).count(),
        "disputes_overdue": overdue().count(),
        "withdrawals_waiting_over_24h": ProductTransaction.objects.filter(
            kind="withdrawal", status="requested", created_at__lt=now - timedelta(hours=24)).count(),
        "safeguarding": SAFEGUARDING_CODE.get(latest.status if latest else "no_data", 1),
    }


def log_ops_snapshot() -> dict:
    snap = ops_snapshot()
    logger.info("ops snapshot", extra={"metric": "ops", **snap})
    return snap
