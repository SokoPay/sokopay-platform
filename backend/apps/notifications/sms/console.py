"""Development SMS provider: logs the message instead of sending it.

Also keeps the last messages in memory so tests can assert what would have been sent
without any network calls.
"""

from __future__ import annotations

import logging

from .base import SmsProvider, SmsResult

logger = logging.getLogger("sokopay.sms")


class ConsoleSmsProvider(SmsProvider):
    name = "console"

    # Test/inspection buffer (most recent last). Never relied on in production.
    sent: list[tuple[str, str]] = []

    def send(self, to: str, message: str) -> SmsResult:
        self.sent.append((to, message))
        logger.info("[SMS:console] to=%s | %s", to, message)
        return SmsResult(success=True, provider_ref="console", message="logged")

    @classmethod
    def reset(cls) -> None:
        cls.sent.clear()
