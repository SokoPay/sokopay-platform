"""
Merchant KYB documents (owner Ghana Card, business registration, TIN certificate, photo
of premises): upload, encrypted storage, and retrieval for staff review.

* Only PDF, JPEG and PNG files up to 5 MB are accepted, checked by their first bytes
  (the browser's declared type and the file name are not trusted).
* Contents are encrypted with the field-encryption key before they reach the disk
  (FIELD_ENCRYPTION_KEY), so a copied volume or backup doesn't expose ID documents.
* Files are never served from a public URL: staff fetch them through
  portal:merchant_document, which checks their role and decrypts on the fly.
"""

from __future__ import annotations

import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.common.encryption import decrypt_bytes, encrypt_bytes

from .exceptions import MerchantError
from .models import Merchant, MerchantDocument

MAX_BYTES = 5 * 1024 * 1024
_TYPES = (  # (magic bytes, extension, content type)
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
)
_CONTENT_TYPES = {ext: ctype for _, ext, ctype in _TYPES}


class DocumentError(MerchantError):
    """The upload isn't an accepted file (type or size)."""


def _sniff(data: bytes) -> str:
    for magic, ext, _ in _TYPES:
        if data.startswith(magic):
            return ext
    raise DocumentError("Upload a PDF, JPEG or PNG file.")


def store(merchant: Merchant, kind: str, upload) -> MerchantDocument:
    """Validate, encrypt and save one uploaded file as a KYB document of `kind`."""
    if kind not in MerchantDocument.Kind.values:
        raise DocumentError("Unknown document type.")
    data = upload.read(MAX_BYTES + 1)
    if not data:
        raise DocumentError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise DocumentError("Files must be 5 MB or smaller.")
    ext = _sniff(data)
    key = f"kyb/{merchant.pk}/{kind}-{uuid.uuid4().hex}.{ext}.enc"
    saved = default_storage.save(key, ContentFile(encrypt_bytes(data)))
    return MerchantDocument.objects.create(merchant=merchant, kind=kind, storage_key=saved)


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
            if upload.size > MAX_BYTES:
                raise DocumentError(f"{MerchantDocument.Kind(kind).label}: files must be 5 MB or smaller.")
            head = upload.read(16)
            upload.seek(0)
            try:
                _sniff(head)
            except DocumentError:
                raise DocumentError(f"{MerchantDocument.Kind(kind).label}: upload a PDF, JPEG or PNG file.") from None


def read(doc: MerchantDocument) -> tuple[bytes, str, str]:
    """Decrypted contents, content type and a download file name."""
    if not doc.storage_key:
        raise DocumentError("This document has no file.")
    with default_storage.open(doc.storage_key, "rb") as fh:
        data = decrypt_bytes(fh.read())
    ext = doc.storage_key.rsplit(".", 2)[-2] if doc.storage_key.endswith(".enc") else "bin"
    name = f"{doc.merchant.short_code or 'merchant'}-{doc.kind}.{ext}"
    return data, _CONTENT_TYPES.get(ext, "application/octet-stream"), name
