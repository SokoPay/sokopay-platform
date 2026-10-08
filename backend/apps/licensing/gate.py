"""
The licence gate: the single place feature code checks what it is allowed to do.

Usage
-----
Imperative check:

    from apps.licensing.gate import require_capability
    from apps.licensing.capabilities import Capability

    def credit_wallet(...):
        require_capability(Capability.HOLD_CUSTOMER_FUNDS)   # raises if not licensed
        ...

As a decorator on a service function or DRF view method:

    from apps.licensing.gate import requires

    @requires(Capability.CASH_IN_OUT)
    def agent_cash_in(...):
        ...

Querying without raising (e.g. to hide a button in a template/app):

    from apps.licensing.gate import is_enabled
    if is_enabled(Capability.MERCHANT_AGGREGATION): ...

The active licence is read from settings.SOKOPAY_ACTIVE_LICENCE. It is resolved
once and cached; changing it requires a deploy/restart, which is deliberate —
unlocking a regulated capability is a controlled, auditable event, not a runtime toggle.
"""

from __future__ import annotations

import functools
import logging

from django.conf import settings

from .capabilities import Capability, Licence, capabilities_for
from .exceptions import CapabilityNotLicensed

logger = logging.getLogger("sokopay.licensing")


def active_licence() -> Licence:
    """The licence this deployment currently operates under."""
    raw = getattr(settings, "SOKOPAY_ACTIVE_LICENCE", Licence.PSP_STANDARD.value)
    try:
        return Licence(raw)
    except ValueError as exc:
        raise CapabilityNotLicensed(
            capability="<startup>", active_licence=raw
        ) from exc


@functools.lru_cache(maxsize=None)
def _enabled_set(licence_value: str) -> frozenset[Capability]:
    """Cached effective capability set for a licence value."""
    return capabilities_for(Licence(licence_value))


def enabled_capabilities() -> frozenset[Capability]:
    """All capabilities enabled for the active licence."""
    return _enabled_set(active_licence().value)


def is_enabled(capability: Capability) -> bool:
    """True if `capability` is permitted under the active licence."""
    return capability in enabled_capabilities()


def require_capability(capability: Capability) -> None:
    """
    Guard a money/regulated action. Raises CapabilityNotLicensed if the active
    licence does not permit `capability`. Call this at the start of any service
    function that performs a regulated activity.
    """
    if not is_enabled(capability):
        lic = active_licence().value
        # Log at warning level so attempts to use not-yet-licensed features during
        # development or by misconfiguration are visible in monitoring.
        logger.warning("Blocked capability %s under licence %s", capability, lic)
        raise CapabilityNotLicensed(capability=str(capability), active_licence=lic)


def requires(capability: Capability):
    """Decorator form of require_capability for service functions and view methods."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            require_capability(capability)
            return func(*args, **kwargs)

        return wrapper

    return decorator
