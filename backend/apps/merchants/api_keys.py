"""
Merchant API key issuing and verification.

A key looks like `sk_live_<prefix>_<secret>`. We store the prefix (for lookup) and a
SHA-256 hash of the whole key (for verification), never the key itself. The raw key is
returned exactly once, at creation.
"""

from __future__ import annotations

import secrets

from django.utils import timezone

from .models import ApiKey, Merchant


def issue_key(merchant: Merchant, mode: str) -> tuple[ApiKey, str]:
    """
    Create a new API key for a merchant. Returns (ApiKey, raw_secret).
    The raw secret is shown to the merchant once and never stored.
    """
    prefix = f"sk_{mode}_{secrets.token_hex(6)}"      # e.g. sk_live_a1b2c3d4e5f6
    secret_part = secrets.token_urlsafe(32)
    raw = f"{prefix}_{secret_part}"
    key = ApiKey.objects.create(
        merchant=merchant,
        mode=mode,
        prefix=prefix,
        key_hash=ApiKey.hash_secret(raw),
    )
    return key, raw


def verify_key(raw: str) -> ApiKey | None:
    """
    Return the active ApiKey for a raw key string, or None if it is unknown, revoked,
    or the hash does not match. Comparison is constant-time.
    """
    # The prefix is the first three underscore-separated parts: sk_<mode>_<hex>.
    parts = raw.split("_")
    if len(parts) < 4 or parts[0] != "sk":
        return None
    prefix = "_".join(parts[:3])

    key = ApiKey.objects.filter(prefix=prefix, revoked_at__isnull=True).first()
    if key is None:
        return None
    if not secrets.compare_digest(key.key_hash, ApiKey.hash_secret(raw)):
        return None

    key.last_used_at = timezone.now()
    key.save(update_fields=["last_used_at"])
    return key


def revoke_key(key: ApiKey) -> None:
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])
