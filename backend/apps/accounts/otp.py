"""
One-time passcodes for phone verification (sign-up and sign-in).

A 6-digit code is generated, stored hashed in the cache for settings.OTP_TTL_SECONDS
(10 minutes by default), and sent to the phone by SMS. `request_otp` returns the raw code to its caller (the view/SMS
sender) so it can be delivered — the code is never put in an HTTP response.

Rate limiting: a limited number of requests per phone per window, and a limited number
of verify attempts per code. In production the cache is Redis; in dev it is in-memory.

`purpose` keeps codes for different jobs apart: "login" (app sign-in and PIN reset, the
default) and "portal2fa" (web portal two-factor). A code issued for one purpose never
verifies for another, and each purpose has its own request limit.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.cache import cache

MAX_VERIFY_ATTEMPTS = 5
MAX_REQUESTS_PER_WINDOW = 5
REQUEST_WINDOW_SECONDS = 60 * 60


class OtpError(Exception):
    """OTP could not be issued or verified (rate limited, expired, wrong code)."""


def ttl_seconds() -> int:
    return int(getattr(settings, "OTP_TTL_SECONDS", 10 * 60))


def ttl_minutes() -> int:
    """Validity in whole minutes, for the SMS text ("It expires in 10 minutes")."""
    return max(1, ttl_seconds() // 60)


def _ns(purpose: str) -> str:
    return "otp" if purpose == "login" else f"otp:{purpose}"


def _code_key(phone: str, purpose: str = "login") -> str:
    return f"{_ns(purpose)}:code:{phone}"


def _attempts_key(phone: str, purpose: str = "login") -> str:
    return f"{_ns(purpose)}:attempts:{phone}"


def _requests_key(phone: str, purpose: str = "login") -> str:
    return f"{_ns(purpose)}:requests:{phone}"


def _hash(code: str) -> str:
    # Keyed hash so a cache leak doesn't reveal codes directly.
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def request_otp(phone: str, purpose: str = "login") -> str:
    """Generate, store and return a new OTP for `phone`. Caller sends it by SMS."""
    requests = cache.get(_requests_key(phone, purpose), 0)
    if requests >= MAX_REQUESTS_PER_WINDOW:
        raise OtpError("Too many codes requested. Try again later.")
    cache.set(_requests_key(phone, purpose), requests + 1, REQUEST_WINDOW_SECONDS)

    code = f"{secrets.randbelow(1_000_000):06d}"
    cache.set(_code_key(phone, purpose), _hash(code), ttl_seconds())
    cache.set(_attempts_key(phone, purpose), 0, ttl_seconds())
    return code


def verify_otp(phone: str, code: str, purpose: str = "login") -> bool:
    """Return True if `code` is the valid, unexpired OTP for `phone`."""
    stored = cache.get(_code_key(phone, purpose))
    if not stored:
        raise OtpError("No code was requested, or it has expired.")

    attempts = cache.get(_attempts_key(phone, purpose), 0)
    if attempts >= MAX_VERIFY_ATTEMPTS:
        cache.delete(_code_key(phone, purpose))
        raise OtpError("Too many attempts. Request a new code.")
    cache.set(_attempts_key(phone, purpose), attempts + 1, ttl_seconds())

    if not hmac.compare_digest(stored, _hash(code)):  # constant-time
        return False

    # One-time use: consume on success.
    cache.delete(_code_key(phone, purpose))
    cache.delete(_attempts_key(phone, purpose))
    return True
