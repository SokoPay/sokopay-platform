"""
Portal security records: 2FA backup codes and a security-event audit trail.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class BackupCode(TimeStampedModel):
    """
    A one-time 2FA recovery code. Only a keyed hash (HMAC-SHA256 with SECRET_KEY) is
    stored: a database leak alone doesn't reveal usable codes, and lookup is a single
    indexed query. Each code works once.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="backup_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "portal_backup_code"

    def __str__(self) -> str:
        return f"Backup code for {self.user_id} ({'used' if self.used_at else 'unused'})"


class SecurityEvent(models.Model):
    """
    Append-only log of security-relevant portal events, for the security team and for
    audits: lockouts, 2FA enrolment and resets, backup-code use. Never stores
    passwords, codes or secrets.
    """

    class Kind(models.TextChoices):
        LOGIN_LOCKED = "login_locked", "Sign-in locked (too many wrong passwords)"
        TWOFA_ENROLLED = "twofa_enrolled", "2FA set up"
        TWOFA_LOCKED = "twofa_locked", "2FA locked (too many wrong codes)"
        TWOFA_RESET = "twofa_reset", "2FA reset by SokoPay staff"
        BACKUP_USED = "backup_used", "Backup code used to sign in"
        BACKUP_REGENERATED = "backup_regenerated", "Backup codes regenerated"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="security_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    ip = models.GenericIPAddressField(null=True, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "portal_security_event"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "kind", "created_at"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.user_id} at {self.created_at:%Y-%m-%d %H:%M}"
