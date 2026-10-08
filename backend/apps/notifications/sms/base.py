"""
SMS provider interface.

Like the payment rails, SMS delivery sits behind one interface with multiple
implementations (console for dev, Hubtel/mNotify for Ghana). Calling code uses
get_sms_provider() and never depends on a specific aggregator.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SmsResult:
    success: bool
    provider_ref: str = ""
    message: str = ""
    raw: dict = field(default_factory=dict)


class SmsProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def send(self, to: str, message: str) -> SmsResult:
        """Send `message` to the E.164 number `to`. Must not raise on delivery
        failure — return SmsResult(success=False) so callers handle it uniformly."""
