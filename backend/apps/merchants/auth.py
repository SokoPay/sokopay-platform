"""
DRF authentication for the merchant API, using API keys.

Clients send:  Authorization: Bearer sk_live_<prefix>_<secret>

On success, request.user is the merchant's owner and request.auth is the ApiKey, so a
view can read request.auth.merchant and request.auth.mode (test/live).
"""

from __future__ import annotations

from rest_framework import authentication, exceptions

from . import api_keys


class MerchantApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None  # not this scheme; let other authenticators try
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")

        raw = header[1].decode()
        key = api_keys.verify_key(raw)
        if key is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")

        merchant = key.merchant
        if not merchant.is_live:
            raise exceptions.AuthenticationFailed("Merchant is not active.")

        # (user, auth) — the owner identifies the caller; the ApiKey carries merchant+mode.
        return (merchant.owner, key)

    def authenticate_header(self, request):
        return self.keyword
