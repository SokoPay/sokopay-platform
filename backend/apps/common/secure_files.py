"""
Encrypted storage for uploaded identity and business documents (ID photos, KYB files).

* The file type is decided by its first bytes, never by the name or the browser's claim.
* Contents are encrypted with FIELD_ENCRYPTION_KEY before they reach the disk, so a
  copied volume or backup doesn't expose anyone's ID.
* Nothing here is served from a public URL: access-checked views decrypt on the fly.
"""

from __future__ import annotations

import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .encryption import decrypt_bytes, encrypt_bytes

MAX_BYTES = 5 * 1024 * 1024
TYPES = {  # extension: (magic bytes, content type)
    "pdf": (b"%PDF-", "application/pdf"),
    "jpg": (b"\xff\xd8\xff", "image/jpeg"),
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
}
IMAGES = ("jpg", "png")
IMAGES_AND_PDF = ("pdf", "jpg", "png")


class FileRejected(ValueError):
    """The upload isn't an accepted file (type, size or empty). Message is safe to show."""


def sniff(head: bytes, allowed=IMAGES_AND_PDF) -> str:
    for ext in allowed:
        if head.startswith(TYPES[ext][0]):
            return ext
    names = [e.upper().replace("JPG", "JPEG") for e in allowed]
    kinds = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
    raise FileRejected(f"Upload a {kinds} file.")


def check(upload, allowed=IMAGES_AND_PDF) -> None:
    """Validate an uploaded file without consuming it."""
    if upload.size > MAX_BYTES:
        raise FileRejected("Files must be 5 MB or smaller.")
    head = upload.read(16)
    upload.seek(0)
    if not head:
        raise FileRejected("The file is empty.")
    sniff(head, allowed)


def store(prefix: str, upload, allowed=IMAGES_AND_PDF) -> str:
    """Validate, encrypt and save `upload` under `prefix`. Returns its storage key."""
    data = upload.read(MAX_BYTES + 1)
    if not data:
        raise FileRejected("The file is empty.")
    if len(data) > MAX_BYTES:
        raise FileRejected("Files must be 5 MB or smaller.")
    ext = sniff(data, allowed)
    key = f"{prefix.rstrip('/')}/{uuid.uuid4().hex}.{ext}.enc"
    return default_storage.save(key, ContentFile(encrypt_bytes(data)))


def read(key: str) -> tuple[bytes, str, str]:
    """Decrypted contents, content type and extension for a stored file."""
    with default_storage.open(key, "rb") as fh:
        data = decrypt_bytes(fh.read())
    ext = key.rsplit(".", 2)[-2] if key.endswith(".enc") else "bin"
    return data, TYPES.get(ext, (b"", "application/octet-stream"))[1], ext
