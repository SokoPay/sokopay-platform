"""
Identity documents added from the customer's profile (see models.IdentityDocument).

  submit(user, ...)       validate the number, expiry and photos; Ghana Card is checked
                          with NIA straight away, passport / driver's licence wait for review
  review(doc, ...)        compliance accepts or rejects a pending document (audited)
  summary(user)           what the app shows on the Profile screen
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.common import secure_files
from apps.common.audit import record as audit
from apps.connectors.identity import GHANA_CARD_RE

from .exceptions import VerificationFailed
from .limits import profile_for
from .models import IdentityDocument, KycProfile

Type = IdentityDocument.Type
Status = IdentityDocument.Status
MAX_SUBMISSIONS_PER_DAY = 10
_PASSPORT_RE = re.compile(r"^[A-Z0-9]{6,12}$")          # Ghana: G1234567; foreign formats vary
_LICENCE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{4,19}$")  # DVLA numbers vary by issue year
NEEDS_EXPIRY = (Type.PASSPORT, Type.DRIVERS_LICENCE)


def _clean_number(doc_type: str, number: str) -> str:
    n = re.sub(r"\s", "", (number or "")).upper()
    if doc_type == Type.GHANA_CARD:
        if not GHANA_CARD_RE.match(n):
            raise VerificationFailed("Enter the Ghana Card number as GHA-123456789-0.")
    elif doc_type == Type.PASSPORT:
        if not _PASSPORT_RE.match(n):
            raise VerificationFailed("Enter the passport number exactly as printed (letters and digits).")
    elif doc_type == Type.DRIVERS_LICENCE:
        if not _LICENCE_RE.match(n):
            raise VerificationFailed("Enter the licence number exactly as printed.")
    else:
        raise VerificationFailed("Choose Ghana Card, passport or driver's licence.")
    return n


def _check_expiry(doc_type: str, expiry: date | None) -> None:
    if doc_type in NEEDS_EXPIRY and expiry is None:
        raise VerificationFailed("Enter the expiry date printed on the document.")
    if expiry is not None and expiry <= timezone.localdate():
        raise VerificationFailed("This document has expired. Please use a valid one.")


def submit(user, *, doc_type: str, number: str, expiry: date | None, front, back=None) -> IdentityDocument:
    if front is None:
        raise VerificationFailed("Add a photo of the front of the document.")
    number = _clean_number(doc_type, number)
    _check_expiry(doc_type, expiry)
    day_ago = timezone.now() - timedelta(days=1)
    if IdentityDocument.objects.filter(user=user, created_at__gte=day_ago).count() >= MAX_SUBMISSIONS_PER_DAY:
        raise VerificationFailed("You've added a lot of documents today. Please try again tomorrow.")
    for upload in (front, back):
        if upload is not None:
            try:
                secure_files.check(upload, secure_files.IMAGES)
            except secure_files.FileRejected as exc:
                raise VerificationFailed(f"Photo: {exc}") from exc

    status = Status.PENDING
    if doc_type == Type.GHANA_CARD:
        profile = profile_for(user)
        if profile.tier >= KycProfile.Tier.VERIFIED:
            if (profile.ghana_card_number or "").upper() != number:
                raise VerificationFailed("This isn't the Ghana Card already verified on your account. "
                                         "Contact support if your card has changed.")
        else:
            from . import services
            services.upgrade_to_verified(user, number)     # NIA check; raises VerificationFailed
        status = Status.VERIFIED

    prefix = f"kyc/{user.pk}/{doc_type}"
    with transaction.atomic():
        IdentityDocument.objects.filter(user=user, doc_type=doc_type, status=Status.PENDING) \
            .update(status=Status.REPLACED)
        doc = IdentityDocument.objects.create(
            user=user, doc_type=doc_type, number=number, number_last4=number[-4:], expiry_date=expiry,
            front_key=secure_files.store(f"{prefix}/front", front, secure_files.IMAGES),
            back_key=secure_files.store(f"{prefix}/back", back, secure_files.IMAGES) if back else "",
            status=status, reviewed_at=timezone.now() if status == Status.VERIFIED else None,
            review_note="Checked with NIA" if status == Status.VERIFIED else "")
        audit("kyc.document_submit", actor=user, obj=doc, doc_type=doc_type, status=status)
    return doc


def review(doc: IdentityDocument, *, actor, approve: bool, note: str = "") -> IdentityDocument:
    if doc.status != Status.PENDING:
        raise VerificationFailed("This document has already been reviewed.")
    if not approve and not note.strip():
        raise VerificationFailed("Say why it isn't accepted; the customer will see this.")
    doc.status = Status.VERIFIED if approve else Status.REJECTED
    doc.review_note = note.strip()[:255]
    doc.reviewed_by = actor
    doc.reviewed_at = timezone.now()
    doc.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at", "updated_at"])
    audit("kyc.document_review", actor=actor, obj=doc, decision=doc.status, note=doc.review_note)
    from apps.notifications.services import notify
    notify(doc.user, kind="info",
           title=f"{doc.get_doc_type_display()} {'verified' if approve else 'not accepted'}",
           body=("Your document is verified." if approve
                 else f"We couldn't accept your document: {doc.review_note}"),
           data={"type": "profile"}, app="customer")
    return doc


def _item(doc: IdentityDocument) -> dict:
    return {
        "id": str(doc.pk),
        "type": doc.doc_type,
        "type_label": doc.get_doc_type_display(),
        "number_hint": f"••••{doc.number_last4}",
        "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
        "status": doc.status,
        "status_label": doc.get_status_display(),
        "note": doc.review_note,
        "submitted_at": doc.created_at.isoformat(),
    }


def summary(user) -> dict:
    profile = KycProfile.objects.filter(user=user).first()
    docs = IdentityDocument.objects.filter(user=user).exclude(status=Status.REPLACED)
    return {
        "ghana_card_verified": bool(profile and profile.ghana_card_hash),
        "ghana_card_hint": profile.ghana_card_masked if profile and profile.ghana_card_hash else "",
        "documents": [_item(d) for d in docs[:20]],
    }
