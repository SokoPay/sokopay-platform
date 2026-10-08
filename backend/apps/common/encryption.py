"""
Field-level encryption for personal data at rest (Ghana Card numbers, account numbers).

* EncryptedCharField stores Fernet (AES-128-CBC + HMAC-SHA256) ciphertext in the
  database and decrypts transparently on read. A database dump alone does not reveal
  the values.
* Encrypted columns can't be searched or made unique, so pair them with
  `lookup_hash()` — a keyed HMAC — when you need "has this card been used before?".

The key is derived from settings.FIELD_ENCRYPTION_KEY (from the environment). Rotating
it requires re-encrypting existing rows; plan that as a migration, never ad hoc.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _key() -> bytes:
    secret = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not secret:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be set to store encrypted fields.")
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def encrypt(value: str) -> str:
    return Fernet(_key()).encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return Fernet(_key()).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "Could not decrypt a field — FIELD_ENCRYPTION_KEY may have changed."
        ) from exc


def lookup_hash(value: str) -> str:
    """Deterministic keyed hash for equality/uniqueness checks on encrypted values."""
    normalised = value.strip().upper()
    return hmac.new(_key(), normalised.encode(), hashlib.sha256).hexdigest()


class EncryptedCharField(models.TextField):
    """A text field encrypted at rest. Not searchable — use lookup_hash() alongside."""

    description = "Text encrypted at rest with Fernet"

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt(value)
