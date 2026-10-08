"""
Arkesel SMS provider (a Ghanaian aggregator): https://developers.arkesel.com

Uses the SMS API v2 "send SMS" operation:
  POST https://sms.arkesel.com/api/v2/sms/send
  header  api-key: <ARKESEL_API_KEY>
  body    {"sender": "<= 11 chars", "message": "...", "recipients": ["233XXXXXXXXX"], "sandbox": bool}
  200     {"status": "success", "data": [{"recipient": "233...", "id": "<uuid>"}]}

ARKESEL_SANDBOX=True sends test requests that Arkesel accepts and records but neither
bills nor delivers (visible only in the Arkesel SMS history report).

Config (from environment, never hardcoded): ARKESEL_API_KEY, ARKESEL_SENDER_ID (falls
back to SMS_SENDER_ID; must be registered and approved on the Arkesel dashboard, max 11
characters), ARKESEL_SANDBOX.

Messages carry one-time codes, so neither the message text nor the API key is ever
logged. TLS verification is always on (the default); never disable it.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from .base import SmsProvider, SmsResult

logger = logging.getLogger("sokopay.sms")

ARKESEL_ENDPOINT = "https://sms.arkesel.com/api/v2/sms/send"
SENDER_MAX_LEN = 11


class ArkeselSmsProvider(SmsProvider):
    name = "arkesel"

    def __init__(self) -> None:
        self.api_key = getattr(settings, "ARKESEL_API_KEY", "")
        sender = getattr(settings, "ARKESEL_SENDER_ID", "") or getattr(settings, "SMS_SENDER_ID", "SokoPay")
        self.sender = sender[:SENDER_MAX_LEN]
        self.sandbox = bool(getattr(settings, "ARKESEL_SANDBOX", False))

    @staticmethod
    def _recipient(to: str) -> str:
        """E.164 (+233244000201) to Arkesel's format (233244000201)."""
        return "".join(ch for ch in to if ch.isdigit())

    def send(self, to: str, message: str) -> SmsResult:
        if not self.api_key:
            logger.error("Arkesel SMS not configured (missing ARKESEL_API_KEY).")
            return SmsResult(success=False, message="sms_not_configured")
        payload = {"sender": self.sender, "message": message, "recipients": [self._recipient(to)]}
        if self.sandbox:
            payload["sandbox"] = True
        try:
            resp = requests.post(
                ARKESEL_ENDPOINT,
                json=payload,
                headers={"api-key": self.api_key, "Accept": "application/json"},
                timeout=15,  # verify=True by default
            )
        except requests.RequestException as exc:
            logger.warning("Arkesel SMS error: %s", type(exc).__name__)
            return SmsResult(success=False, message=type(exc).__name__)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        ok = resp.status_code == 200 and body.get("status") == "success"
        if not ok:
            # Arkesel's error bodies describe the problem (balance, sender ID, key); they
            # don't echo the message, so they're safe to log.
            logger.warning("Arkesel SMS failed: %s %s", resp.status_code, str(body.get("message", ""))[:200])
            return SmsResult(success=False, message=str(resp.status_code), raw=body)
        data = body.get("data") or []
        ref = data[0].get("id", "") if data and isinstance(data[0], dict) else ""
        return SmsResult(success=True, provider_ref=str(ref), message="sandbox" if self.sandbox else "sent",
                         raw=body)
