"""
JWT issuing, verification and revocation for the mobile apps (customer and agent).

Short-lived access tokens, longer-lived refresh tokens, both signed with the Django
SECRET_KEY (HS256). The mobile app sends `Authorization: Bearer <access>` and uses the
refresh token to get a new access token when it expires.

Revocation (two layers, both checked on every authenticated request):
  * a denylist of individual token ids (`jti`) — sign-out, and every refresh token
    once it has been used (rotation: a stolen, already-used refresh token is dead);
  * `User.token_generation` — every token records the generation it was issued under;
    bumping the user's counter is "sign out everywhere" in one write.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import jwt
from django.conf import settings
from django.db.models import F
from django.utils import timezone

ACCESS_TTL_SECONDS = 15 * 60          # 15 minutes
REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
ALGORITHM = "HS256"


class TokenError(Exception):
    """An access/refresh token was missing, malformed, expired, revoked or the wrong type."""


def _encode(user_id: str, token_type: str, ttl: int, generation: int) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "type": token_type,            # "access" | "refresh"
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "gen": int(generation),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def issue_tokens(user) -> dict:
    """Return a fresh access+refresh pair for a user."""
    gen = getattr(user, "token_generation", 0)
    return {
        "access": _encode(user.id, "access", ACCESS_TTL_SECONDS, gen),
        "refresh": _encode(user.id, "refresh", REFRESH_TTL_SECONDS, gen),
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SECONDS,
    }


def decode(token: str, expected_type: str) -> dict:
    """Decode and validate a token's signature, expiry and type (not its revocation)."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM],
                             options={"require": ["sub", "type", "iat", "exp", "jti", "gen"]})
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid token.") from exc
    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token.")
    return payload


# --- revocation -----------------------------------------------------------------
def is_revoked(payload: dict) -> bool:
    from .models import RevokedToken
    return RevokedToken.objects.filter(jti=payload["jti"]).exists()


def stale_generation(payload: dict, user) -> bool:
    """True if the user has signed out everywhere since this token was issued."""
    return int(payload.get("gen", -1)) != int(getattr(user, "token_generation", 0))


def check_live(payload: dict, user) -> None:
    """Raise TokenError if a structurally valid token has been revoked."""
    if stale_generation(payload, user) or is_revoked(payload):
        raise TokenError("Session has ended. Please sign in again.")


def revoke(payload: dict, user) -> None:
    from .models import RevokedToken
    RevokedToken.objects.get_or_create(
        jti=payload["jti"],
        defaults={"user": user, "token_type": payload.get("type", ""),
                  "expires_at": dt.datetime.fromtimestamp(int(payload["exp"]), tz=dt.timezone.utc)},
    )


def revoke_all(user) -> None:
    """Invalidate every token this user holds (atomic increment, safe under concurrency)."""
    type(user).objects.filter(pk=user.pk).update(token_generation=F("token_generation") + 1)
    user.refresh_from_db(fields=["token_generation"])


def purge_expired() -> int:
    from .models import RevokedToken
    deleted, _ = RevokedToken.objects.filter(expires_at__lt=timezone.now()).delete()
    return deleted


def access_from_refresh(refresh_token: str) -> dict:
    """
    Mint a new access+refresh pair from a valid refresh token, and retire the old
    refresh token so it can't be used twice.
    """
    from django.contrib.auth import get_user_model

    payload = decode(refresh_token, "refresh")
    user = get_user_model().objects.filter(pk=payload["sub"], is_active=True).first()
    if user is None:
        raise TokenError("Invalid token.")
    check_live(payload, user)
    revoke(payload, user)                       # rotation: one use per refresh token
    return issue_tokens(user)
