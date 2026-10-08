"""DRF authentication using the mobile JWT access token."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import authentication, exceptions

from . import tokens

User = get_user_model()


class JWTAuthentication(authentication.BaseAuthentication):
    """
    Authenticate `Authorization: Bearer <access-jwt>`.

    Returns None (lets other authenticators run) when there is no Bearer header, so the
    browsable API and session auth still work in development. Raises when a Bearer token
    is present but invalid, expired, or revoked (signed out / signed out everywhere).
    """

    keyword = b"bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword:
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")

        try:
            payload = tokens.decode(header[1].decode(), expected_type="access")
        except tokens.TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc)) from exc

        try:
            user = User.objects.get(pk=payload["sub"], is_active=True)
        except User.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed("User not found.") from exc

        try:
            tokens.check_live(payload, user)
        except tokens.TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc)) from exc
        return (user, payload)

    def authenticate_header(self, request):
        return "Bearer"
