"""
Rate limiting for the web portals.

Fixed-window counters in the shared cache (Redis in production, so every API task sees
the same counts). Identifiers — phone numbers, user ids, IPs — are hashed before they
become cache keys, so the cache never holds personal data in the clear.

Two kinds of use:
  * failure counters (login, 2FA): `is_blocked()` before checking credentials, `hit()`
    after a failure, `reset()` after success. Once blocked, even the right password or
    code is refused until the window passes — otherwise the lockout is an oracle.
  * action throttles (bulk upload, API keys, settlements…): `allow()` consumes one unit
    and returns False when the caller is over the limit.

Limits are defaults below; override any of them with settings.PORTAL_RATE_LIMITS,
e.g. {"login_phone": (5, 900)} — (max events, window seconds).

The WAF adds a coarser per-IP limit at the edge (infra/alb.tf, "auth-rate-limit").
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("sokopay.security")

DEFAULT_RULES: dict[str, tuple[int, int]] = {
    # failures
    "login_phone": (5, 15 * 60),        # wrong passwords for one phone
    "login_ip": (30, 15 * 60),          # wrong passwords from one IP (credential stuffing)
    "twofa_user": (5, 15 * 60),         # wrong 2FA / backup codes for one user
    "invite_ip": (20, 15 * 60),         # invalid invite links from one IP (token guessing)
    "pw_reset_phone": (5, 15 * 60),     # wrong password-reset codes for one phone
    # actions (per IP: public pages)
    "pw_reset_request_ip": (10, 60 * 60),
    "register_ip": (5, 60 * 60),
    # actions (per user)
    "team_invite": (20, 60 * 60),
    "password_change": (10, 60 * 60),
    "bulk_upload": (20, 60 * 60),
    "api_key_create": (10, 60 * 60),
    "qr_request": (120, 60 * 60),
    "settlement_request": (10, 60 * 60),
    "settlement_account": (5, 60 * 60),
    "backup_regen": (5, 60 * 60),
    "merchant_refund": (30, 60 * 60),
    "dispute_open": (10, 24 * 60 * 60),
    "webhook_test": (20, 60 * 60),
    "statement": (30, 60 * 60),
    "xb_quote": (60, 60 * 60),
    # hosted checkout: each attempt pushes a MoMo prompt to a phone
    "checkout_ip": (30, 60 * 60),
    "checkout_phone": (6, 60 * 60),
}


def rule(name: str) -> tuple[int, int]:
    overrides = getattr(settings, "PORTAL_RATE_LIMITS", {}) or {}
    return tuple(overrides.get(name, DEFAULT_RULES[name]))


def _key(name: str, ident: str) -> str:
    digest = hashlib.sha256(f"{name}:{ident}".encode()).hexdigest()[:40]
    return f"rl:{name}:{digest}"


def count(name: str, ident: str) -> int:
    return int(cache.get(_key(name, ident), 0))


def is_blocked(name: str, ident: str) -> bool:
    limit, _ = rule(name)
    return count(name, ident) >= limit


def hit(name: str, ident: str) -> int:
    """Record one event; returns the count in the current window."""
    key = _key(name, ident)
    _, window = rule(name)
    if cache.add(key, 1, window):           # first event starts the window
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:                      # expired between add() and incr()
        cache.set(key, 1, window)
        return 1


def reset(name: str, ident: str) -> None:
    cache.delete(_key(name, ident))


def allow(name: str, ident: str) -> bool:
    """Consume one unit of an action throttle. False = over the limit (refuse)."""
    limit, _ = rule(name)
    n = hit(name, ident)
    if n > limit:
        if n == limit + 1:                  # log once per window, not every refusal
            logger.warning("Rate limit %s exceeded", name)
        return False
    return True


def client_ip(request) -> str:
    """
    The caller's IP. Behind the load balancer REMOTE_ADDR is the ALB, so we read
    X-Forwarded-For — but only the entries our own proxies appended. With
    TRUSTED_PROXY_COUNT = N, the client is the N-th entry from the right; anything to
    its left was supplied by the client and is ignored (it can be forged). With 0
    (local development) X-Forwarded-For is ignored entirely.
    """
    remote = request.META.get("REMOTE_ADDR", "") or "unknown"
    hops = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    if hops <= 0:
        return remote
    chain = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    if len(chain) >= hops:
        return chain[-hops]
    return remote
