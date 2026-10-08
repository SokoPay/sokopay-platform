"""
Portal passwords without anyone handling them: invite links, SMS password reset, and
password change.

* Invites: staff adding a merchant owner, or an owner/admin adding a team member,
  triggers an SMS with a one-time link (/dashboard/invite/<token>/) where the person
  sets their own password. Only a hash of the token is stored; it expires after
  PORTAL_INVITE_TTL_HOURS and works once.
* Forgot password: a 6-digit code by SMS (accounts.otp, purpose "portal_reset", same
  lifetime and limits as other codes). The answer is the same whether or not the number
  has a portal account, so the page can't be used to discover who has one.
* Setting or resetting a password never skips 2FA: the next sign-in still asks for a code.

Every step is recorded as a SecurityEvent (never with the password, code or token).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone

from apps.accounts import otp
from apps.notifications.sms import get_sms_provider

from . import security
from .models import PortalInvite, SecurityEvent

User = get_user_model()
RESET_PURPOSE = "portal_reset"


class InviteError(Exception):
    """An invite or password step couldn't be completed (message is safe to show)."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def ttl_hours() -> int:
    return int(getattr(settings, "PORTAL_INVITE_TTL_HOURS", 72))


def ttl_text() -> str:
    hours = ttl_hours()
    if hours % 24 == 0:
        days = hours // 24
        return "1 day" if days == 1 else f"{days} days"
    return "1 hour" if hours == 1 else f"{hours} hours"


def _first_name(user) -> str:
    return (user.full_name or "").split(" ")[0] or "Hello"


# --- invites --------------------------------------------------------------------------
def create_invite(user, *, created_by=None, merchant=None, role: str = "") -> str:
    """A fresh one-time token for `user` (older open invites are cancelled). Returns the raw token."""
    now = timezone.now()
    PortalInvite.objects.filter(user=user, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)
    raw = secrets.token_urlsafe(32)
    PortalInvite.objects.create(user=user, merchant=merchant, role=role, token_hash=_hash(raw),
                                expires_at=now + timedelta(hours=ttl_hours()), created_by=created_by)
    return raw


def send_invite(user, *, link_for, created_by=None, merchant=None, role: str = "", ip=None) -> bool:
    """
    Create an invite and text the link. `link_for(raw_token)` builds the absolute URL
    (the view passes request.build_absolute_uri). Returns whether the SMS was accepted.
    """
    raw = create_invite(user, created_by=created_by, merchant=merchant, role=role)
    business = (merchant.trading_name or merchant.legal_name) if merchant else "SokoPay"
    message = (f"SokoPay: {_first_name(user)}, you've been added to the business portal for {business}. "
               f"Set your password here: {link_for(raw)} (expires in {ttl_text()}).")
    result = get_sms_provider().send(user.phone, message)
    security.record(SecurityEvent.Kind.INVITE_SENT, user=user, actor=created_by, ip=ip,
                    detail=f"{business}{'' if result.success else ' (SMS failed)'}")
    return result.success


def open_invite(raw: str) -> PortalInvite | None:
    """The unexpired, unused invite for this token, or None."""
    if not raw:
        return None
    return (PortalInvite.objects.select_related("user", "merchant")
            .filter(token_hash=_hash(raw), used_at__isnull=True, expires_at__gt=timezone.now()).first())


def redeem_invite(raw: str, password: str, *, ip=None):
    """Set the invited person's password. Raises InviteError or Django's ValidationError."""
    invite = open_invite(raw)
    if invite is None:
        raise InviteError("This link has expired or was already used. Ask for a new invite.")
    user = invite.user
    validate_password(password, user=user)
    with transaction.atomic():
        invite = PortalInvite.objects.select_for_update().get(pk=invite.pk)
        if invite.used_at is not None:
            raise InviteError("This link was already used.")
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        invite.used_at = timezone.now()
        invite.save(update_fields=["used_at", "updated_at"])
    security.record(SecurityEvent.Kind.PASSWORD_SET, user=user, ip=ip)
    return user


# --- forgot / change password --------------------------------------------------------
def _portal_user(phone: str):
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None or getattr(user, "closed_at", None) is not None or not security.is_portal_user(user):
        return None
    return user


def request_password_reset(phone: str) -> None:
    """Text a reset code if `phone` is an active portal user. Silent otherwise (no enumeration)."""
    user = _portal_user(phone)
    if user is None:
        return
    try:
        code = otp.request_otp(phone, purpose=RESET_PURPOSE)
    except otp.OtpError:
        return                       # rate limited: same answer as everyone else
    get_sms_provider().send(phone, f"Your SokoPay password reset code is {code}. It expires in "
                                   f"{otp.ttl_minutes()} minutes. If you didn't ask for this, ignore this message.")


def reset_password(phone: str, code: str, password: str, *, ip=None):
    """Check the SMS code and set a new password. Raises InviteError or ValidationError."""
    user = _portal_user(phone)
    if user is None:
        raise InviteError("That code is not valid.")
    validate_password(password, user=user)          # before spending the code on a weak password
    try:
        ok = otp.verify_otp(phone, code.strip(), purpose=RESET_PURPOSE)
    except otp.OtpError as exc:
        raise InviteError(str(exc)) from exc
    if not ok:
        raise InviteError("That code is not valid.")
    user.set_password(password)
    user.save(update_fields=["password", "updated_at"])
    PortalInvite.objects.filter(user=user, used_at__isnull=True).update(expires_at=timezone.now())
    security.record(SecurityEvent.Kind.PASSWORD_RESET, user=user, ip=ip)
    return user


def change_password(user, current: str, new: str, *, ip=None) -> None:
    if not user.check_password(current):
        raise InviteError("Your current password is not correct.")
    validate_password(new, user=user)
    user.set_password(new)
    user.save(update_fields=["password", "updated_at"])
    security.record(SecurityEvent.Kind.PASSWORD_CHANGED, user=user, ip=ip)
