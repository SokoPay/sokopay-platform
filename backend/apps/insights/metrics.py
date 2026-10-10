"""
Platform figures for the back office: the live overview, the compliance and risk picture,
and payments & settlement. Aggregates only (counts and pesewa totals), cached for a few
seconds so a wall of open dashboards can't load the database.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

from . import transactions

User = get_user_model()
LIVE_CACHE_SECONDS = 10
OPEN_ALERT = ("open", "investigating", "escalated")


def _cached(key: str, fn, seconds: int = LIVE_CACHE_SECONDS):
    return cache.get_or_set(f"insights:{key}", fn, seconds)


# --- users -----------------------------------------------------------------------------------
def users() -> dict:
    today = timezone.localdate()
    start, _ = transactions.day_bounds(today)
    people = User.objects.exclude(user_type="staff")
    by_type = dict(people.values_list("user_type").annotate(n=Count("id")))
    from apps.kyc.models import KycProfile
    tiers = dict(KycProfile.objects.values_list("tier").annotate(n=Count("id")))
    return {
        "total": people.count(),
        "customers": by_type.get("consumer", 0),
        "merchant_users": by_type.get("merchant", 0),
        "agents": by_type.get("agent", 0),
        "new_today": people.filter(created_at__gte=start).count(),
        "new_7d": people.filter(created_at__gte=start - dt.timedelta(days=6)).count(),
        "disabled": people.filter(is_active=False, closed_at__isnull=True).count(),
        "closed": people.filter(closed_at__isnull=False).count(),
        "held": KycProfile.objects.filter(frozen=True).count(),
        "tiers": {0: tiers.get(0, 0), 1: tiers.get(1, 0), 2: tiers.get(2, 0)},
    }


# --- system alerts -----------------------------------------------------------------------------
SYSTEM_LABELS = {
    "stuck_settlements": ("Merchant payouts stuck", "portal:admin_payments"),
    "stuck_refunds": ("Refunds stuck", "portal:admin_payments"),
    "transfers_pending_over_1h": ("Transfers pending over 1 hour", "portal:admin_transactions"),
    "cross_border_pending_over_1h": ("Cross-border sends pending over 1 hour", "portal:admin_transactions"),
    "lifestyle_pending_over_1h": ("Ticket/food orders pending over 1 hour", "portal:admin_transactions"),
    "webhooks_abandoned_1h": ("Merchant webhooks abandoned (1 hour)", "portal:ops_health"),
    "recon_breaks_open": ("Reconciliation breaks open", "portal:ops_recon"),
    "disputes_overdue": ("Disputes past their deadline", "portal:ops_disputes"),
    "withdrawals_waiting_over_24h": ("Savings withdrawals waiting over 24 hours", "portal:ops_withdrawals"),
}


def system_alerts() -> list[dict]:
    """Things that need a person now, from the same snapshot the monitoring alarms use."""
    from apps.common.health import ops_snapshot
    snap = ops_snapshot()
    out = [{"key": k, "label": label, "count": snap.get(k, 0), "url": url}
           for k, (label, url) in SYSTEM_LABELS.items() if snap.get(k, 0)]
    payments = snap.get("payments_15m", 0)
    failed = snap.get("payments_failed_15m", 0)
    if payments >= 10 and failed / payments >= 0.25:
        out.insert(0, {"key": "payment_failures", "label": f"{failed} of {payments} payments failed in 15 minutes",
                       "count": failed, "url": "portal:admin_transactions", "severity": "high"})
    if snap.get("safeguarding", 0) >= 2:
        out.insert(0, {"key": "safeguarding", "label": "Safeguarding shortfall: trust funds below e-money issued",
                       "count": 1, "url": "portal:safeguarding", "severity": "high"})
    for item in out:
        item.setdefault("severity", "medium")
    return out


# --- compliance ----------------------------------------------------------------------------------
def compliance() -> dict:
    """The compliance picture, with an overall status: green / amber / red."""
    from apps.compliance.models import Alert, ScreeningMatch, SuspiciousTransactionReport
    from apps.kyc.models import IdentityDocument
    from apps.merchants.models import Merchant
    now = timezone.now()
    open_alerts = Alert.objects.filter(status__in=OPEN_ALERT)
    by_sev = dict(open_alerts.values_list("severity").annotate(n=Count("id")))
    overdue_high = open_alerts.filter(severity="high", created_at__lt=now - dt.timedelta(hours=48)).count()
    overdue_any = open_alerts.filter(created_at__lt=now - dt.timedelta(days=7)).count()
    screening_pending = ScreeningMatch.objects.filter(status="pending").count()
    from apps.kyc.models import KycProfile
    held_users = KycProfile.objects.filter(frozen=True).values("user_id")
    screening_confirmed_open = (ScreeningMatch.objects.filter(status="confirmed", user__isnull=False)
                                .exclude(user_id__in=held_users).count())
    data = {
        "alerts_high": by_sev.get("high", 0),
        "alerts_medium": by_sev.get("medium", 0),
        "alerts_low": by_sev.get("low", 0),
        "alerts_unassigned": open_alerts.filter(assigned_to__isnull=True).count(),
        "overdue_high": overdue_high,
        "overdue_any": overdue_any,
        "screening_pending": screening_pending,
        "screening_confirmed_unheld": screening_confirmed_open,
        "str_drafts": SuspiciousTransactionReport.objects.filter(status="draft").count(),
        "str_to_file": SuspiciousTransactionReport.objects.filter(status="approved").count(),
        "id_docs_pending": IdentityDocument.objects.filter(status="pending").count(),
        "merchants_in_review": Merchant.objects.filter(status__in=("submitted", "in_review")).count(),
        "high_risk_merchants": Merchant.objects.filter(risk_tier="high", status="approved").count(),
    }
    reasons = []
    if overdue_high:
        reasons.append(f"{overdue_high} high-severity AML alert(s) open over 48 hours")
    if screening_confirmed_open:
        reasons.append(f"{screening_confirmed_open} confirmed sanctions/PEP match(es) without a hold")
    if data["str_to_file"]:
        reasons.append(f"{data['str_to_file']} approved STR(s) not yet filed with the FIC")
    if reasons:
        status = "red"
    elif any(data[k] for k in ("alerts_high", "overdue_any", "screening_pending", "str_drafts", "id_docs_pending")):
        status = "amber"
        reasons = ["Items are waiting for compliance review"]
    else:
        status = "green"
        reasons = ["Nothing waiting"]
    data["status"] = status
    data["reasons"] = reasons
    return data


# --- payments & settlement ----------------------------------------------------------------------
def payments_and_settlement(days: int = 7) -> dict:
    from apps.connectors.registry import catalogue
    from apps.merchants import settlement as settlement_svc
    from apps.merchants.models import Refund, Settlement
    from apps.payments.models import RailEvent
    from apps.reconciliation.models import ReconItem, ReconRun
    from django.conf import settings
    since = timezone.now() - dt.timedelta(days=days)
    paid = Settlement.objects.filter(status="paid", completed_at__gte=since)
    rails = []
    for provider in sorted(set(RailEvent.objects.values_list("rail", flat=True)) |
                           {getattr(settings, "RAIL_PROVIDER", "mock")}):
        last = RailEvent.objects.filter(rail=provider).order_by("-created_at").first() if provider else None
        rails.append({"name": provider, "active": provider == getattr(settings, "RAIL_PROVIDER", "mock"),
                      "last_event": last.created_at if last else None})
    gateways = [r for r in catalogue() if r.get("category") in ("telco", "bank", "transfer", "card", "identity",
                                                                  "cross_border", "remittance")]
    return {
        "paid_count": paid.count(),
        "paid_value": int(paid.aggregate(v=Sum("amount_minor"))["v"] or 0),
        "awaiting_approval": Settlement.objects.filter(status="awaiting_approval").select_related("merchant")
                                               .order_by("created_at")[:50],
        "processing": Settlement.objects.filter(status="processing").select_related("merchant")
                                        .order_by("created_at")[:50],
        "stuck": settlement_svc.stuck_settlements().count(),
        "failed_recent": Settlement.objects.filter(status="failed", updated_at__gte=since)
                                           .select_related("merchant").order_by("-updated_at")[:20],
        "paid_recent": paid.select_related("merchant").order_by("-completed_at")[:20],
        "refunds_processing": Refund.objects.filter(status="processing").count(),
        "rails": rails,
        "rail_provider": getattr(settings, "RAIL_PROVIDER", "mock"),
        "gateways": gateways,
        "recon_runs": ReconRun.objects.order_by("-date")[:7],
        "recon_breaks_open": ReconItem.objects.filter(resolved=False).count(),
    }


def work_queues() -> dict:
    """Queues that need a decision from someone in the back office."""
    from apps.compliance.models import Alert
    from apps.merchants import settlement as settlement_svc
    from apps.merchants.models import Merchant, Settlement
    return {
        "aml_high_open": Alert.objects.filter(severity="high", status__in=OPEN_ALERT).count(),
        "payouts_stuck": settlement_svc.stuck_settlements().count(),
        "merchants_review": Merchant.objects.filter(status__in=("submitted", "in_review")).count(),
        "settlements_awaiting": Settlement.objects.filter(status="awaiting_approval").count(),
        "merchants_approved": Merchant.objects.filter(status="approved").count(),
    }


# --- live overview --------------------------------------------------------------------------------
def overview() -> dict:
    """Everything on the dashboard, cached for LIVE_CACHE_SECONDS."""
    def build():
        today = timezone.localdate()
        return {
            "as_of": timezone.now(),
            "users": users(),
            "today": transactions.day_summary(today),
            "yesterday": transactions.day_summary(today - dt.timedelta(days=1)),
            "series": transactions.daily_series(14, today),
            "system_alerts": system_alerts(),
            "compliance": compliance(),
            "work": work_queues(),
        }
    return _cached("overview", build)
