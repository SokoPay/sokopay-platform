"""
Inbound remittance connectors: licensed international money-transfer partners that
deliver remittances into SokoPay wallets ("inbound remittance termination", a DEMI
activity).

The partner converts the foreign currency and settles cedis to SokoPay; it notifies us
of each payment by signed webhook. We verify the signature, then credit the wallet.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings

from .base import RemittanceConnector
from .placeholder import PlaceholderRemittance
from .types import InboundRemittanceEvent


class RemittancePartnerConnector(PlaceholderRemittance):
    key = "remittance_partner"
    display_name = "Remittance partner (template)"
    regulator = "Bank of Ghana (via the partner's licensed bank/IMTO arrangement)"
    go_live = ("Termination agreement with a licensed international money-transfer "
               "operator or its partner bank; webhook spec and signing secret; "
               "settlement account and FX terms. [VERIFY]")


class MockRemittanceConnector(RemittanceConnector):
    """Dev/test double: HMAC-SHA256 over the body with REMITTANCE_WEBHOOK_SECRET."""

    key = "mock"
    display_name = "Mock remittance partner"
    regulator = "—"
    go_live = "Never used in production."
    available = True
    is_mock = True

    @staticmethod
    def _secret() -> bytes:
        return (getattr(settings, "REMITTANCE_WEBHOOK_SECRET", "") or "mock-remit").encode()

    @classmethod
    def sign(cls, body: bytes) -> str:
        return hmac.new(cls._secret(), body, hashlib.sha256).hexdigest()

    def verify_and_parse(self, headers, body):
        sent = headers.get("x-mock-signature", "")
        ok = hmac.compare_digest(sent, self.sign(body))
        try:
            data = json.loads(body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            data = {}
        return InboundRemittanceEvent(
            partner_ref=str(data.get("partner_ref", "")),
            recipient_phone=str(data.get("recipient_phone", "")),
            amount_minor=int(data.get("amount_minor", 0) or 0),
            currency=str(data.get("currency", "")),
            sender_name=str(data.get("sender_name", "")),
            sender_country=str(data.get("sender_country", "")),
            signature_ok=ok,
        )
