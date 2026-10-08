"""
Portal security records: 2FA backup codes, the SMS 2FA device, portal invites and a
security-event audit trail.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django_otp.models import Device

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
        INVITE_SENT = "invite_sent", "Portal invite sent"
        PASSWORD_SET = "password_set", "Password set from an invite"
        PASSWORD_RESET = "password_reset", "Password reset with an SMS code"
        PASSWORD_CHANGED = "password_changed", "Password changed"

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


class SmsDevice(Device):
    """
    Portal 2FA by SMS: texts a one-time code to the user's phone through the configured
    SMS provider (Arkesel in production). One per user, created on first use when
    PORTAL_2FA_SMS is on. Codes live hashed in the cache (apps.accounts.otp, purpose
    "portal2fa"), expire after OTP_TTL_SECONDS, are single-use, and share the OTP
    request/attempt limits. A code issued for app sign-in never works here, or vice versa.
    """

    PURPOSE = "portal2fa"

    class Meta:
        db_table = "portal_sms_device"

    def generate_challenge(self) -> bool:
        """Text a new code. True if the provider accepted it; raises OtpError when rate limited."""
        from apps.accounts import otp
        from apps.notifications.sms import get_sms_provider

        code = otp.request_otp(self.user.phone, purpose=self.PURPOSE)
        result = get_sms_provider().send(
            self.user.phone,
            f"Your SokoPay sign-in code is {code}. It expires in {otp.ttl_minutes()} minutes. "
            "Never share it, not even with SokoPay staff.",
        )
        return result.success

    def verify_token(self, token) -> bool:
        from apps.accounts import otp

        token = str(token or "")
        if not token.isdigit() or len(token) != 6:
            return False
        try:
            return otp.verify_otp(self.user.phone, token, purpose=self.PURPOSE)
        except otp.OtpError:
            return False


class PortalInvite(TimeStampedModel):
    """
    A one-time link that lets an invited person set their own portal password: a
    merchant owner added by staff, or a team member added by an owner or admin. Nobody
    else ever sees or sends the password.

    Only a SHA-256 hash of the token is stored; the raw token exists only in the SMS.
    It expires after PORTAL_INVITE_TTL_HOURS, works once, and a newer invite for the
    same person cancels any older ones.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="portal_invites")
    merchant = models.ForeignKey("merchants.Merchant", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="+")
    role = models.CharField(max_length=16, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="+")

    class Meta:
        db_table = "portal_invite"
        indexes = [models.Index(fields=["user", "used_at"])]

    def __str__(self) -> str:
        return f"Invite for {self.user_id} ({'used' if self.used_at else 'open'})"
