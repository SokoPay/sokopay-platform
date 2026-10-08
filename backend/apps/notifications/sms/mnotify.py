"""
mNotify SMS provider (a Ghanaian aggregator). Alternative/secondary to Hubtel.

STATUS: implemented against mNotify's documented quick-send API shape; confirm the
endpoint/fields against current docs before go-live. [VERIFY]

Config: MNOTIFY_API_KEY, SMS_SENDER_ID (from environment). TLS verification stays on.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from .base import SmsProvider, SmsResult

logger = logging.getLogger("sokopay.sms")

MNOTIFY_ENDPOINT = "https://api.mnotify.com/api/sms/quick"


class MnotifySmsProvider(SmsProvider):
    name = "mnotify"

    def __init__(self) -> None:
        self.api_key = getattr(settings, "MNOTIFY_API_KEY", "")
        self.sender = getattr(settings, "SMS_SENDER_ID", "SokoPay")

    def send(self, to: str, message: str) -> SmsResult:
        if not self.api_key:
            logger.error("mNotify SMS not configured (missing API key).")
            return SmsResult(success=False, message="sms_not_configured")
        try:
            resp = requests.post(
                MNOTIFY_ENDPOINT,
                params={"key": self.api_key},
                json={"recipient": [to], "sender": self.sender, "message": message},
                timeout=15,
            )
            ok = resp.status_code == 200
            if not ok:
                logger.warning("mNotify SMS failed: %s %s", resp.status_code, resp.text[:200])
            return SmsResult(success=ok, message=str(resp.status_code))
        except requests.RequestException as exc:
            logger.exception("mNotify SMS error")
            return SmsResult(success=False, message=str(exc))
