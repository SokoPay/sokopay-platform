from __future__ import annotations

import functools

from django.conf import settings

from .base import PushProvider
from .console import ConsolePushProvider
from .fcm import FcmPushProvider

_REGISTRY: dict[str, type[PushProvider]] = {
    "console": ConsolePushProvider,
    "fcm": FcmPushProvider,
}


@functools.lru_cache(maxsize=None)
def get_push_provider(name: str | None = None) -> PushProvider:
    name = (name or getattr(settings, "PUSH_PROVIDER", "console")).lower()
    return _REGISTRY.get(name, ConsolePushProvider)()


def reset_push_cache() -> None:
    get_push_provider.cache_clear()
