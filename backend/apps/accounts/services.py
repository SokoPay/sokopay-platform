"""
Consumer authentication services: OTP sign-in/up, PIN set, PIN login with lockout.

Flow for the mobile app:
  1. request_otp(phone)                      → SMS code sent
  2. verify_otp_and_login(phone, code)       → user created/returned + JWT tokens
  3. set_pin(user, pin)                       → PIN set for future quick sign-in
  4. login_with_pin(phone, pin)               → JWT tokens (daily use)
  5. refresh(refresh_token)                   → new access token
"""

from __future__ import annotations

import re

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from . import otp, tokens
from .models import UserType

User = get_user_model()

MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT = timedelta(minutes=5)


class AuthError(Exception):
    """Authentication failed (bad PIN, locked out, weak PIN, etc.)."""


# --- OTP sign-in / sign-up --------------------------------------------------
def request_otp(phone: str) -> str:
    """Issue an OTP and return it so the caller (view) can send it by SMS."""
    return otp.request_otp(phone)


def verify_otp_and_login(phone: str, code: str) -> tuple:
    """
    Verify an OTP. Returns (user, tokens, is_new).

    An SMS code alone proves only that someone holds the SIM — which a SIM-swap
    fraudster can. So for an existing customer who has a PIN, the code is NOT enough:
    tokens is None and the app must ask for the PIN next (/auth/login). New customers
    (no PIN yet) get a session so they can create their PIN.
    """
    if not otp.verify_otp(phone, code):
        raise AuthError("That code is not correct.")
    existing = User.objects.filter(phone=phone).first()
    if existing is not None and existing.closed_at is not None:
        raise AuthError("This account was closed. Contact SokoPay support to reopen it.")
    if existing is not None and not existing.is_active:
        raise AuthError("This account is not active. Contact SokoPay support.")
    if existing is not None and existing.has_pin:
        return existing, None, False
    user, is_new = User.objects.get_or_create(phone=phone, defaults={"user_type": UserType.CONSUMER})
    return user, tokens.issue_tokens(user), is_new


# --- PIN ---------------------------------------------------------------------
_WEAK_PINS = {"000000", "111111", "123456", "654321", "121212", "112233"}


def _validate_pin(pin: str) -> None:
    if not (pin.isdigit() and len(pin) == 6):
        raise AuthError("PIN must be exactly 6 digits.")
    if pin in _WEAK_PINS or len(set(pin)) == 1:
        raise AuthError("Please choose a less predictable PIN.")


def set_pin(user, pin: str) -> None:
    """Create the FIRST PIN. Changing one needs the current PIN (change_pin) or a reset."""
    if user.has_pin:
        raise AuthError("You already have a PIN. Use Change PIN, or Forgot PIN if you can't remember it.")
    _validate_pin(pin)
    user.set_pin(pin)
    user.save(update_fields=["pin_hash", "updated_at"])


def change_pin(user, current_pin: str, new_pin: str) -> dict:
    """Change the PIN (current PIN required). Signs out every other device."""
    confirm_pin(user, current_pin)                # shared lockout
    _validate_pin(new_pin)
    if user.check_pin(new_pin):
        raise AuthError("Choose a PIN different from your current one.")
    user.set_pin(new_pin)
    user.save(update_fields=["pin_hash", "updated_at"])
    tokens.revoke_all(user)
    return tokens.issue_tokens(user)


# --- forgot PIN ----------------------------------------------------------------------
PIN_RESET_COOLDOWN = timedelta(hours=24)


def request_pin_reset(phone: str) -> str | None:
    """
    Issue a reset code if (and only if) there is an active account. The API answers the
    same either way, so it can't be used to find out who has a SokoPay account.
    """
    user = User.objects.filter(phone=phone, is_active=True, closed_at__isnull=True).first()
    if user is None or not user.has_pin:
        return None
    return otp.request_otp(phone)


def reset_pin(*, phone: str, code: str, new_pin: str, ghana_card: str = "") -> tuple:
    """
    Forgot PIN: SMS code + (for Ghana Card–verified customers) the card number, then a
    new PIN. Afterwards: every other device signed out, and sends capped for 24 hours —
    so a SIM-swap fraudster who gets this far still can't empty the wallet.
    Returns (user, tokens).
    """
    _validate_pin(new_pin)
    if not otp.verify_otp(phone, code):
        raise AuthError("That code is not correct.")
    user = User.objects.filter(phone=phone, is_active=True, closed_at__isnull=True).first()
    if user is None:
        raise AuthError("That code is not correct.")
    from apps.kyc.models import KycProfile
    profile = KycProfile.objects.filter(user=user).first()
    if profile is not None and profile.ghana_card_hash:
        from apps.common.encryption import lookup_hash
        card = (ghana_card or "").strip().upper()
        if not card:
            raise AuthError("Enter your Ghana Card number to reset your PIN. Request a new code to try again.")
        if lookup_hash(card) != profile.ghana_card_hash:
            raise AuthError("That Ghana Card number doesn't match this account. Request a new code to try again.")
    user.set_pin(new_pin)
    user.pin_reset_at = timezone.now()
    user.failed_login_count, user.locked_until = 0, None
    user.save(update_fields=["pin_hash", "pin_reset_at", "failed_login_count", "locked_until", "updated_at"])
    tokens.revoke_all(user)
    from apps.notifications.services import notify
    notify(user, kind="security", title="Your PIN was reset",
           body="Your SokoPay PIN was just reset and other phones were signed out. "
                "If this wasn't you, call SokoPay support immediately.", data={"type": "security"}, sms=True)
    return user, tokens.issue_tokens(user)


# --- profile -------------------------------------------------------------------------
_GPS_RE = re.compile(r"^([A-Z]{2})(\d{3,4})(\d{4})$")


def normalise_gps(value: str) -> str:
    """Ghana Post GPS digital address → canonical "GA-183-8164" ("" stays "")."""
    compact = re.sub(r"[\s-]", "", (value or "").upper())
    if not compact:
        return ""
    m = _GPS_RE.match(compact)
    if not m:
        raise AuthError("Enter your Ghana Post GPS address like GA-183-8164 (it's in the GhanaPostGPS app).")
    return "-".join(m.groups())


def update_profile(user, *, full_name: str | None = None, email: str | None = None,
                   address: str | None = None, gps_address: str | None = None):
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    fields = []
    if full_name is not None:
        name = " ".join(full_name.split())
        from apps.kyc.models import KycProfile
        locked = KycProfile.objects.filter(user=user, ghana_card_hash__isnull=False).exists()
        if locked and name != user.full_name:
            raise AuthError("Your name comes from your verified Ghana Card and can't be changed here. "
                            "Contact support if it's wrong.")
        if not (2 <= len(name) <= 150) or not any(ch.isalpha() for ch in name):
            raise AuthError("Enter your full name.")
        if name != user.full_name:
            user.full_name = name
            fields.append("full_name")
    if email is not None:
        email = email.strip().lower()
        if email:
            try:
                validate_email(email)
            except ValidationError as exc:
                raise AuthError("Enter a valid email address.") from exc
        user.email = email
        fields.append("email")
    if address is not None:
        address = " ".join(address.split())
        if address and (len(address) < 5 or not any(ch.isalpha() for ch in address)):
            raise AuthError("Enter your house number, street and town.")
        user.address = address[:255]
        fields.append("address")
    if gps_address is not None:
        user.gps_address = normalise_gps(gps_address)
        fields.append("gps_address")
    if fields:
        user.save(update_fields=[*fields, "updated_at"])
        if "full_name" in fields:
            from apps.compliance.screening import screen_user   # AML: rescreen a new name
            screen_user(user)
    return user


# --- close account ---------------------------------------------------------------------
def close_account(user, *, pin: str, reason: str = "") -> None:
    """
    Close the account at the customer's request. Refused while money or obligations
    remain, or while compliance holds it. Records are retained (AML Act) and anonymised
    after the retention period; the phone number stays reserved until then.
    """
    confirm_pin(user, pin)
    from apps.kyc.models import KycProfile
    if KycProfile.objects.filter(user=user, frozen=True).exists():
        raise AuthError("This account can't be closed right now. Please contact SokoPay support.")
    from apps.ledger import accounts
    from apps.ledger.services import natural_balance_of
    if natural_balance_of(accounts.customer_wallet(str(user.id))) != 0:
        raise AuthError("Your wallet still has money in it. Send or withdraw it before closing your account.")
    from apps.payments.models import Payment
    from apps.wallet.models import ExternalTransfer
    if (Payment.objects.filter(user=user, status__in=("created", "pending")).exists()
            or ExternalTransfer.objects.filter(sender=user, status="pending").exists()):
        raise AuthError("You have a payment still in progress. Try again once it has finished.")
    if user.merchant_memberships.filter(role="owner").exists():
        raise AuthError("You own a business on SokoPay. Close or hand over the business first.")
    if hasattr(user, "agent_profile"):
        raise AuthError("Agent accounts are closed by SokoPay operations. Please contact support.")
    from apps.agents.models import CashOutRequest
    CashOutRequest.objects.filter(customer=user, status="pending").update(status="declined", decided_at=timezone.now())
    from apps.notifications.models import Device
    Device.objects.filter(user=user).update(active=False)
    user.is_active = False
    user.closed_at = timezone.now()
    user.closed_reason = (reason or "")[:255]
    user.save(update_fields=["is_active", "closed_at", "closed_reason", "updated_at"])
    tokens.revoke_all(user)


def anonymise_closed_accounts() -> int:
    """After the retention period, strip personal data from closed accounts."""
    import uuid as _uuid

    from django.conf import settings as dj_settings
    years = int(getattr(dj_settings, "DATA_RETENTION_YEARS", 5))
    cutoff = timezone.now() - timedelta(days=365 * years)
    done = 0
    for user in User.objects.filter(closed_at__lt=cutoff, anonymised_at__isnull=True):
        user.full_name, user.email, user.pin_hash = "", "", ""
        user.phone = f"+0{_uuid.uuid4().hex[:14]}"          # frees the real number
        user.set_unusable_password()
        user.anonymised_at = timezone.now()
        user.save()
        from apps.kyc.models import KycProfile
        KycProfile.objects.filter(user=user).update(ghana_card_number="", verified_name="", ghana_card_hash=None)
        done += 1
    return done


# --- sign out ---------------------------------------------------------------------
def logout(user, access_payload: dict | None, refresh_token: str | None = None) -> None:
    """End this session: the current access token and (if supplied) its refresh token."""
    if access_payload:
        tokens.revoke(access_payload, user)
    if refresh_token:
        try:
            payload = tokens.decode(refresh_token, "refresh")
        except tokens.TokenError:
            return                                   # already dead; nothing to do
        if payload["sub"] == str(user.id):           # never let A revoke B's token
            tokens.revoke(payload, user)


def logout_everywhere(user) -> None:
    tokens.revoke_all(user)


LOGIN_FAILED = "Wrong phone number or PIN."


def login_with_pin(phone: str, pin: str) -> dict:
    """
    Return JWT tokens for a phone+PIN login, enforcing lockout. A wrong PIN and an unknown
    number get the same answer in about the same time, so the login screen can't be used
    to find out who has a SokoPay account.
    """
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None:
        from django.contrib.auth.hashers import check_password
        check_password(pin or "", _DUMMY_PIN_HASH())          # spend the same hashing time
        raise AuthError(LOGIN_FAILED)
    try:
        _check_pin_with_lockout(user, pin)
    except AuthError as exc:
        if str(exc) == "Wrong PIN.":
            raise AuthError(LOGIN_FAILED) from exc
        raise
    return tokens.issue_tokens(user)


def _DUMMY_PIN_HASH() -> str:  # noqa: N802 - computed once, then cached
    global _dummy_hash
    if _dummy_hash is None:
        from django.contrib.auth.hashers import make_password
        _dummy_hash = make_password("not-a-real-pin")
    return _dummy_hash


_dummy_hash = None


def confirm_pin(user, pin: str) -> None:
    """
    Step-up check for a sensitive action by a signed-in user (e.g. requesting a
    settlement, adding a payout account). Shares the sign-in lockout counter, so PIN
    guesses can't be spread across the login screen and in-app prompts.
    """
    user.refresh_from_db(fields=["failed_login_count", "locked_until", "pin_hash"])
    _check_pin_with_lockout(user, pin or "")


def _check_pin_with_lockout(user, pin: str) -> None:
    now = timezone.now()
    if user.locked_until and user.locked_until > now:
        raise AuthError("Account is temporarily locked. Try again shortly.")

    if not user.has_pin:
        raise AuthError("No PIN set. Sign in with a one-time code first.")

    if not user.check_pin(pin):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_PIN_ATTEMPTS:
            user.locked_until = now + PIN_LOCKOUT
            user.failed_login_count = 0
        user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])
        raise AuthError("Wrong PIN.")

    # Success: clear any failed-attempt state.
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_count", "locked_until", "updated_at"])


def refresh(refresh_token: str) -> dict:
    try:
        return tokens.access_from_refresh(refresh_token)
    except tokens.TokenError as exc:
        raise AuthError(str(exc)) from exc
