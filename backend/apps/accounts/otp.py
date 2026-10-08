"""
One-time passcodes for phone verification (sign-up and sign-in).

A 6-digit code is generated, stored hashed in the cache with a short TTL, and sent to
the phone by SMS. `request_otp` returns the raw code to its caller (the view/SMS
sender) so it can be delivered — the code is never put in an HTTP response.

Rate limiting: a limited number of requests per phone per window, and a limited number
of verify attempts per code. In production the cache is Redis; in dev it is in-memory.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.cache import cache

CODE_TTL_SECONDS = 5 * 60
MAX_VERIFY_ATTEMPTS = 5
MAX_REQUESTS_PER_WINDOW = 5
REQUEST_WINDOW_SECONDS = 60 * 60


class OtpError(Exception):
    """OTP could not be issued or verified (rate limited, expired, wrong code)."""


def _code_key(phone: str) -> str:
    return f"otp:code:{phone}"


def _attempts_key(phone: str) -> str:
    return f"otp:attempts:{phone}"


def _requests_key(phone: str) -> str:
    return f"otp:requests:{phone}"


def _hash(code: str) -> str:
    # Keyed hash so a cache leak doesn't reveal codes directly.
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def request_otp(phone: str) -> str:
    """Generate, store and return a new OTP for `phone`. Caller sends it by SMS."""
    requests = cache.get(_requests_key(phone), 0)
    if requests >= MAX_REQUESTS_PER_WINDOW:
        raise OtpError("Too many codes requested. Try again later.")
    cache.set(_requests_key(phone), requests + 1, REQUEST_WINDOW_SECONDS)

    code = f"{secrets.randbelow(1_000_000):06d}"
    cache.set(_code_key(phone), _hash(code), CODE_TTL_SECONDS)
    cache.set(_attempts_key(phone), 0, CODE_TTL_SECONDS)
    return code


def verify_otp(phone: str, code: str) -> bool:
    """Return True if `code` is the valid, unexpired OTP for `phone`."""
    stored = cache.get(_code_key(phone))
    if not stored:
        raise OtpError("No code was requested, or it has expired.")

    attempts = cache.get(_attempts_key(phone), 0)
    if attempts >= MAX_VERIFY_ATTEMPTS:
        cache.delete(_code_key(phone))
        raise OtpError("Too many attempts. Request a new code.")
    cache.set(_attempts_key(phone), attempts + 1, CODE_TTL_SECONDS)

    if not hmac.compare_digest(stored, _hash(code)):  # constant-time
        return False

    # One-time use: consume on success.
    cache.delete(_code_key(phone))
    cache.delete(_attempts_key(phone))
    return True
