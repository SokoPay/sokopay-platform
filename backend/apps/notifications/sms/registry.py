"""SMS provider factory. Picks the provider named by settings.SMS_PROVIDER."""

from __future__ import annotations

import functools

from django.conf import settings

from .base import SmsProvider
from .console import ConsoleSmsProvider
from .hubtel import HubtelSmsProvider
from .mnotify import MnotifySmsProvider

_REGISTRY: dict[str, type[SmsProvider]] = {
    "console": ConsoleSmsProvider,
    "hubtel": HubtelSmsProvider,
    "mnotify": MnotifySmsProvider,
}


@functools.lru_cache(maxsize=None)
def get_sms_provider(name: str | None = None) -> SmsProvider:
    name = (name or getattr(settings, "SMS_PROVIDER", "console")).lower()
    cls = _REGISTRY.get(name, ConsoleSmsProvider)
    return cls()


def reset_sms_cache() -> None:
    get_sms_provider.cache_clear()
