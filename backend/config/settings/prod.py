"""
Production settings: the shared hardening (hardened.py) plus "real money only" checks:
no mock integrations, a configured and sandbox-certified payment partner.
"""

from django.core.exceptions import ImproperlyConfigured

from .hardened import *  # noqa: F401,F403
from .hardened import env  # noqa: F401

# --- No test doubles in production, whatever the environment says ---
# A mock rail or connector would accept forged callbacks and credit real balances.
ALLOW_MOCK_INTEGRATIONS = False
_mock_routes = [
    name for name, value in {
        "RAIL_PROVIDER": RAIL_PROVIDER,                       # noqa: F405
        "KYC_IDENTITY_PROVIDER": KYC_IDENTITY_PROVIDER,       # noqa: F405
        **{f"INTEROP_ROUTES[{k}]": v for k, v in INTEROP_ROUTES.items()},   # noqa: F405
    }.items() if str(value).lower() == "mock"
]
if _mock_routes:
    raise ImproperlyConfigured(
        "Production cannot route through mock integrations: " + ", ".join(_mock_routes)
    )
_partner = RAIL_PARTNERS.get(RAIL_PROVIDER, {})  # noqa: F405
_partner_missing = [k for k in ("base_url", "client_id", "client_secret", "webhook_secret",
                                "callback_url") if not _partner.get(k)]
if _partner_missing:
    raise ImproperlyConfigured(
        f"Rail {RAIL_PROVIDER!r} is missing: {', '.join(_partner_missing)} "  # noqa: F405
        f"(env {RAIL_PROVIDER.upper()}_*)."  # noqa: F405
    )
if RAIL_PROVIDER not in RAIL_CERTIFIED_PARTNERS:  # noqa: F405
    raise ImproperlyConfigured(
        f"Rail {RAIL_PROVIDER!r} has not passed sandbox certification "  # noqa: F405
        "(add it to RAIL_CERTIFIED_PARTNERS only after docs/RAILS-INTEGRATION.md)."
    )

