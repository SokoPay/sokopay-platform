"""Push-notification provider interface (console for dev, FCM for production)."""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PushResult:
    success: bool
    invalid_token: bool = False   # True = the device token is dead; stop using it
    message: str = ""


class PushProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def send(self, token: str, title: str, body: str, data: dict | None = None) -> PushResult:
        """Deliver one notification. Must not raise on delivery failure."""
