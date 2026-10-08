"""Development push provider: logs instead of sending; keeps a buffer for tests."""

from __future__ import annotations

import logging

from .base import PushProvider, PushResult

logger = logging.getLogger("sokopay.push")


class ConsolePushProvider(PushProvider):
    name = "console"
    sent: list[dict] = []

    def send(self, token, title, body, data=None):
        if token.startswith("dead-"):          # test hook: a token FCM would reject
            return PushResult(success=False, invalid_token=True, message="UNREGISTERED")
        self.sent.append({"token": token, "title": title, "body": body, "data": data or {}})
        logger.info("[PUSH:console] %s | %s — %s", token[:12], title, body)
        return PushResult(success=True)

    @classmethod
    def reset(cls) -> None:
        cls.sent.clear()
