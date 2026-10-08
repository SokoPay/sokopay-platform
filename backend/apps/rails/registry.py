"""
Rail registry and factory.

`get_rail()` returns the rail configured for this deployment (settings.RAIL_PROVIDER),
or a named one. Instances are cached per name. Payments/settlement code calls
`get_rail()` and never imports a concrete rail class directly.
"""

from __future__ import annotations

import functools

from django.conf import settings

from .base import RailProvider
from .exceptions import UnknownRail
from .korba import KorbaRail
from .mock import MockRail
from .nsano import NsanoRail

_REGISTRY: dict[str, type[RailProvider]] = {
    "mock": MockRail,
    "korba": KorbaRail,
    "nsano": NsanoRail,
}


@functools.lru_cache(maxsize=None)
def get_rail(name: str | None = None) -> RailProvider:
    """Return the configured (or named) rail provider instance."""
    name = (name or getattr(settings, "RAIL_PROVIDER", "mock")).lower()
    try:
        cls = _REGISTRY[name]
    except KeyError as exc:
        raise UnknownRail(f"No rail provider registered under {name!r}.") from exc
    # The in-process MockRail accepts anything; it only exists where mocks are allowed.
    if cls is MockRail and not getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False):
        raise UnknownRail(f"No rail provider registered under {name!r}.")
    return cls()


def reset_rail_cache() -> None:
    """Clear the cached rail instances (used by tests that switch RAIL_PROVIDER)."""
    get_rail.cache_clear()
