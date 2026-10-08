"""
Alert lifecycle: raise (with de-duplication), assign, note, hold/release, close.

Every action writes an AlertNote, so each alert carries its own audit trail.
Holds reuse the KYC wallet freeze (money can still come IN, nothing goes OUT).
Customers are never told why — the app only says the wallet is on hold and to contact
support (no tipping-off, as the AML Act requires).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.common.audit import audited

from .models import Alert, AlertNote

logger = logging.getLogger("sokopay.aml")

MAX_EVIDENCE = 50
DEFAULT_AUTO_HOLD_RULES = {"SANCTIONS_MATCH"}


def auto_hold_rules() -> set[str]:
    return set(getattr(settings, "AML_AUTO_HOLD_RULES", DEFAULT_AUTO_HOLD_RULES))


def raise_alert(*, rule: str, title: str, severity: str, subject_kind: str, subject_id: str,
                subject_label: str, user=None, evidence: dict | None = None) -> Alert:
    """
    Open an alert, or — if the same rule is already open on the same subject — add the
    new evidence to it (one case per pattern, not one per transaction).
    """
    with transaction.atomic():
        alert = (Alert.objects.select_for_update()
                 .filter(subject_kind=subject_kind, subject_id=str(subject_id), rule=rule,
                         status__in=(Alert.Status.OPEN, Alert.Status.INVESTIGATING, Alert.Status.ESCALATED))
                 .first())
        if alert:
            alert.hits += 1
            alert.last_seen = timezone.now()
            if evidence:
                alert.evidence = (alert.evidence + [evidence])[-MAX_EVIDENCE:]
            alert.save(update_fields=["hits", "last_seen", "evidence", "updated_at"])
            return alert
        alert = Alert.objects.create(
            rule=rule, title=title[:160], severity=severity, subject_kind=subject_kind,
            subject_id=str(subject_id), subject_label=subject_label[:160], user=user,
            evidence=[evidence] if evidence else [],
        )
        AlertNote.objects.create(alert=alert, kind=AlertNote.Kind.SYSTEM, text=f"Raised by rule {rule}.")
        logger.warning("AML alert %s %s on %s %s", rule, severity, subject_kind, subject_id)
        if rule in auto_hold_rules() and user is not None:
            place_hold(alert, actor=None, reason="Automatic hold pending compliance review.")
    return alert


def add_note(alert: Alert, *, author, text: str, kind: str = AlertNote.Kind.NOTE) -> AlertNote:
    text = (text or "").strip()
    if not text:
        raise ValueError("Note can't be empty.")
    return AlertNote.objects.create(alert=alert, author=author, kind=kind, text=text[:4000])


@audited("aml.assign", obj="alert")
def assign(alert: Alert, *, actor) -> Alert:
    alert.assigned_to = actor
    if alert.status == Alert.Status.OPEN:
        alert.status = Alert.Status.INVESTIGATING
    alert.save(update_fields=["assigned_to", "status", "updated_at"])
    add_note(alert, author=actor, kind=AlertNote.Kind.ASSIGN, text=f"Assigned to {actor}.")
    return alert


@audited("aml.escalate", obj="alert", fields=("reason",))
def escalate(alert: Alert, *, actor, reason: str) -> Alert:
    alert.status = Alert.Status.ESCALATED
    alert.save(update_fields=["status", "updated_at"])
    add_note(alert, author=actor, kind=AlertNote.Kind.STATUS, text=f"Escalated: {reason or '—'}")
    return alert


@audited("aml.close", obj="alert", fields=("reason", "reported"))
def close(alert: Alert, *, actor, reason: str, reported: bool = False) -> Alert:
    """Close with a written reason (required: the regulator will ask why)."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to close an alert.")
    alert.status = Alert.Status.CLOSED_REPORTED if reported else Alert.Status.CLOSED_NO_ACTION
    alert.closed_reason = reason[:255]
    alert.closed_at = timezone.now()
    alert.save(update_fields=["status", "closed_reason", "closed_at", "updated_at"])
    add_note(alert, author=actor, kind=AlertNote.Kind.STATUS,
             text=f"Closed ({alert.get_status_display()}): {reason}")
    return alert


@audited("aml.hold", obj="alert", fields=("reason",))
def place_hold(alert: Alert, *, actor, reason: str) -> None:
    """Freeze the subject's wallet (money in still allowed, money out blocked)."""
    from apps.kyc import services as kyc
    if alert.user is None:
        raise ValueError("This alert has no customer wallet to hold.")
    # Internal reason — never shown to the customer.
    kyc.freeze(alert.user, reason=f"AML review (alert {alert.id}): {reason}"[:255], actor=actor)
    AlertNote.objects.create(alert=alert, author=actor, kind=AlertNote.Kind.HOLD,
                             text=f"Wallet placed on hold. {reason}")


@audited("aml.release", obj="alert", fields=("reason",))
def release_hold(alert: Alert, *, actor, reason: str) -> None:
    from apps.kyc import services as kyc
    if alert.user is None:
        raise ValueError("This alert has no customer wallet.")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to release a hold.")
    kyc.unfreeze(alert.user, actor=actor)
    AlertNote.objects.create(alert=alert, author=actor, kind=AlertNote.Kind.RELEASE,
                             text=f"Hold released. {reason}")
