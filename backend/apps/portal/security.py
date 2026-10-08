"""
Portal two-factor policy, backup codes and security events.

Who needs 2FA:
  * SokoPay staff — always.
  * Merchant users whose role can move money (settings.MERCHANT_2FA_REQUIRED_ROLES,
    default Owner, Admin, Finance) — always.
  * Anyone else (Cashier, Developer) — optional, but once they have enrolled their
    sessions must pass it too.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from apps.common.audit import audited

from .models import BackupCode, SecurityEvent

logger = logging.getLogger("sokopay.security")

DEFAULT_REQUIRED_ROLES = ("owner", "admin", "finance")
BACKUP_CODE_COUNT = 10
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: no I, L, O, U


# --- policy ------------------------------------------------------------------------
def has_confirmed_device(user) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def merchant_role_requires_2fa(role: str) -> bool:
    return role in getattr(settings, "MERCHANT_2FA_REQUIRED_ROLES", DEFAULT_REQUIRED_ROLES)


def needs_2fa(user, merchant_role: str | None = None) -> bool:
    if getattr(user, "user_type", None) == "staff":
        return True
    if merchant_role and merchant_role_requires_2fa(merchant_role):
        return True
    return has_confirmed_device(user)


def is_portal_user(user) -> bool:
    return (getattr(user, "user_type", None) == "staff"
            or user.merchant_memberships.exists())


# --- backup codes ------------------------------------------------------------------
def normalise_code(code: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]", "", code or "").upper()
    return cleaned.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def _hash(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"backup:{normalise_code(code)}".encode(),
                    hashlib.sha256).hexdigest()


def generate_backup_codes(user, n: int = BACKUP_CODE_COUNT) -> list[str]:
    """Replace the user's backup codes. Returns the new codes — show them exactly once."""
    codes = []
    with transaction.atomic():
        BackupCode.objects.filter(user=user).delete()
        for _ in range(n):
            raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))   # 50 bits
            codes.append(f"{raw[:5]}-{raw[5:]}")
        BackupCode.objects.bulk_create([BackupCode(user=user, code_hash=_hash(c)) for c in codes])
    return codes


def consume_backup_code(user, code: str) -> bool:
    """Use a backup code. True once per code; a single UPDATE makes it race-safe."""
    if len(normalise_code(code)) != 10:
        return False
    used = BackupCode.objects.filter(
        user=user, code_hash=_hash(code), used_at__isnull=True
    ).update(used_at=timezone.now(), updated_at=timezone.now())
    return used == 1


def backup_codes_left(user) -> int:
    return BackupCode.objects.filter(user=user, used_at__isnull=True).count()


@audited("2fa.reset", obj="user")
def reset_2fa(user, *, actor, ip: str | None = None) -> None:
    """Staff action: remove a user's authenticator + backup codes so they re-enrol."""
    with transaction.atomic():
        TOTPDevice.objects.filter(user=user).delete()
        BackupCode.objects.filter(user=user).delete()
        record(SecurityEvent.Kind.TWOFA_RESET, user=user, actor=actor, ip=ip)


# --- audit -------------------------------------------------------------------------
def record(kind: str, *, user=None, actor=None, ip: str | None = None, detail: str = "") -> None:
    try:
        ip = str(ipaddress.ip_address(ip)) if ip else None
    except ValueError:
        ip = None
    SecurityEvent.objects.create(kind=kind, user=user, actor=actor, ip=ip, detail=detail[:255])
    logger.warning("security_event kind=%s user=%s actor=%s",
                   kind, getattr(user, "id", None), getattr(actor, "id", None))
