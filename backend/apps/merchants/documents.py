"""
Merchant KYB documents (owner Ghana Card, business registration, TIN certificate, photo
of premises): upload, encrypted storage, and retrieval for staff review.

PDF, JPEG and PNG files up to 5 MB, checked by their first bytes and encrypted at rest
(see apps.common.secure_files). Staff fetch them through portal:merchant_document, which
checks their role and decrypts on the fly.
"""

from __future__ import annotations

from apps.common import secure_files

from .exceptions import MerchantError
from .models import Merchant, MerchantDocument

MAX_BYTES = secure_files.MAX_BYTES


class DocumentError(MerchantError):
    """The upload isn't an accepted file (type or size)."""


def store(merchant: Merchant, kind: str, upload) -> MerchantDocument:
    """Validate, encrypt and save one uploaded file as a KYB document of `kind`."""
    if kind not in MerchantDocument.Kind.values:
        raise DocumentError("Unknown document type.")
    try:
        key = secure_files.store(f"kyb/{merchant.pk}/{kind}", upload)
    except secure_files.FileRejected as exc:
        raise DocumentError(str(exc)) from exc
    return MerchantDocument.objects.create(merchant=merchant, kind=kind, storage_key=key)


def store_all(merchant: Merchant, files) -> list[MerchantDocument]:
    """Save every provided upload from a form's FILES, keyed by document kind."""
    saved = []
    for kind in MerchantDocument.Kind.values:
        upload = files.get(f"doc_{kind}")
        if upload:
            saved.append(store(merchant, kind, upload))
    return saved


def validate_all(files) -> None:
    """Check uploads before anything is created, so a bad file doesn't leave half an application."""
    for kind in MerchantDocument.Kind.values:
        upload = files.get(f"doc_{kind}")
        if upload:
            try:
                secure_files.check(upload)
            except secure_files.FileRejected as exc:
                raise DocumentError(f"{MerchantDocument.Kind(kind).label}: {exc}") from None


def read(doc: MerchantDocument) -> tuple[bytes, str, str]:
    """Decrypted contents, content type and a download file name."""
    if not doc.storage_key:
        raise DocumentError("This document has no file.")
    data, content_type, ext = secure_files.read(doc.storage_key)
    return data, content_type, f"{doc.merchant.short_code or 'merchant'}-{doc.kind}.{ext}"
