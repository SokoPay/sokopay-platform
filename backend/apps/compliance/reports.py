"""
Regulatory reporting: suspicious-transaction reports (STRs) and large-transaction
reports, for the Financial Intelligence Centre (FIC).

STR workflow (four-eyes):
  draft (officer A, from one or more alerts) → approve (officer B ≠ A) → filed (with the
  FIC's reference). Filing closes the linked alerts as "reported".
Exports are structured JSON / CSV ready to transcribe into the FIC's goAML portal.
[VERIFY: map fields to the FIC goAML schema with the MLRO.]
"""

from __future__ import annotations

import csv
import io
import json

from django.db import transaction
from django.utils import timezone
from apps.common.audit import audited

from . import alerts as alert_service
from .models import Alert, LargeTransactionReport, SuspiciousTransactionReport


class ReportError(ValueError):
    pass


@audited("str.draft", actor="prepared_by")
def draft_str(*, alerts: list[Alert], prepared_by, narrative: str) -> SuspiciousTransactionReport:
    narrative = (narrative or "").strip()
    if len(narrative) < 50:
        raise ReportError("Write a narrative of at least 50 characters: who, what, when, why it's suspicious.")
    if not alerts:
        raise ReportError("Pick at least one alert.")
    subjects = {(a.subject_kind, a.subject_id) for a in alerts}
    if len(subjects) > 1:
        raise ReportError("All alerts on one report must be about the same subject.")
    first = alerts[0]
    snapshot = []
    if first.user is not None:
        from apps.activity.services import feed
        snapshot = feed(first.user, period="30d", size=200)["results"]
    with transaction.atomic():
        report = SuspiciousTransactionReport.objects.create(
            user=first.user, subject_label=first.subject_label, narrative=narrative,
            transactions=snapshot, prepared_by=prepared_by)
        report.alerts.set(alerts)
        for a in alerts:
            alert_service.add_note(a, author=prepared_by, text=f"STR drafted ({report.id}).")
    return report


@audited("str.approve", actor="approver", obj="report")
def approve_str(report: SuspiciousTransactionReport, *, approver) -> SuspiciousTransactionReport:
    if report.status != SuspiciousTransactionReport.Status.DRAFT:
        raise ReportError("Only a draft can be approved.")
    if approver.id == report.prepared_by_id:
        raise ReportError("A second compliance officer must approve the report.")
    report.status = SuspiciousTransactionReport.Status.APPROVED
    report.approved_by, report.approved_at = approver, timezone.now()
    report.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    return report


@audited("str.filed", obj="report", fields=("fic_reference",))
def mark_filed(report: SuspiciousTransactionReport, *, actor, fic_reference: str) -> SuspiciousTransactionReport:
    if report.status != SuspiciousTransactionReport.Status.APPROVED:
        raise ReportError("Approve the report before filing it.")
    fic_reference = (fic_reference or "").strip()
    if not fic_reference:
        raise ReportError("Enter the FIC submission reference.")
    with transaction.atomic():
        report.status = SuspiciousTransactionReport.Status.FILED
        report.filed_at, report.fic_reference = timezone.now(), fic_reference[:64]
        report.save(update_fields=["status", "filed_at", "fic_reference", "updated_at"])
        for a in report.alerts.all():
            if a.is_open:
                alert_service.close(a, actor=actor, reason=f"Reported to FIC ({fic_reference}).", reported=True)
    return report


def export_str_json(report: SuspiciousTransactionReport) -> bytes:
    user = report.user
    kyc = getattr(user, "kyc", None) if user else None
    body = {
        "report_id": str(report.id),
        "reporting_entity": "SokoPay (Akaditi Limited)",
        "status": report.status,
        "prepared_by": str(report.prepared_by),
        "approved_by": str(report.approved_by) if report.approved_by else None,
        "subject": {
            "label": report.subject_label,
            "phone": user.phone if user else None,
            "full_name": user.full_name if user else None,
            "verified_name": kyc.verified_name if kyc else None,
            "kyc_tier": kyc.tier if kyc else None,
        },
        "narrative": report.narrative,
        "alerts": [{"rule": a.rule, "severity": a.severity, "first_seen": a.first_seen.isoformat(),
                    "hits": a.hits, "evidence": a.evidence} for a in report.alerts.all()],
        "transactions": report.transactions,
        "fic_reference": report.fic_reference or None,
    }
    return json.dumps(body, indent=2, default=str).encode()


def export_large_txns_csv(*, mark_exported: bool = True) -> bytes:
    """Unexported large-transaction reports as CSV (then marked exported)."""
    rows = LargeTransactionReport.objects.filter(exported_at__isnull=True).select_related("user").order_by("occurred_at")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["occurred_at", "customer_phone", "customer_name", "direction", "kind", "amount_ghs", "entry"])
    ids = []
    for r in rows:
        ids.append(r.id)
        w.writerow([r.occurred_at.isoformat(), r.user.phone, r.user.full_name, r.direction, r.kind,
                    f"{r.amount_minor / 100:.2f}", r.entry_id])
    if mark_exported and ids:
        LargeTransactionReport.objects.filter(id__in=ids).update(exported_at=timezone.now())
    return ("﻿" + buf.getvalue()).encode()
