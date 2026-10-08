"""
Hubtel SMS provider (a Ghanaian aggregator).

STATUS: implemented against Hubtel's documented quick-send API shape, but the exact
endpoint/fields must be confirmed against the current Hubtel docs and a sandbox send
before go-live. [VERIFY]

Config (from environment, never hardcoded):
  HUBTEL_CLIENT_ID, HUBTEL_CLIENT_SECRET, SMS_SENDER_ID

TLS verification is always on (the default); never disable it.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from .base import SmsProvider, SmsResult

logger = logging.getLogger("sokopay.sms")

HUBTEL_ENDPOINT = "https://sms.hubtel.com/v1/messages/send"


class HubtelSmsProvider(SmsProvider):
    name = "hubtel"

    def __init__(self) -> None:
        self.client_id = getattr(settings, "HUBTEL_CLIENT_ID", "")
        self.client_secret = getattr(settings, "HUBTEL_CLIENT_SECRET", "")
        self.sender = getattr(settings, "SMS_SENDER_ID", "SokoPay")

    def send(self, to: str, message: str) -> SmsResult:
        if not (self.client_id and self.client_secret):
            logger.error("Hubtel SMS not configured (missing client id/secret).")
            return SmsResult(success=False, message="sms_not_configured")
        try:
            resp = requests.post(
                HUBTEL_ENDPOINT,
                json={"From": self.sender, "To": to, "Content": message},
                auth=(self.client_id, self.client_secret),
                timeout=15,  # verify=True by default
            )
            ok = resp.status_code in (200, 201)
            if not ok:
                logger.warning("Hubtel SMS failed: %s %s", resp.status_code, resp.text[:200])
            return SmsResult(
                success=ok,
                provider_ref=(resp.json().get("MessageId", "") if ok else ""),
                message=str(resp.status_code),
            )
        except requests.RequestException as exc:
            logger.exception("Hubtel SMS error")
            return SmsResult(success=False, message=str(exc))
