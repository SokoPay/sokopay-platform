class RailError(Exception):
    """Base class for rail (Enhanced PSP partner) errors."""


class WebhookVerificationError(RailError):
    """An inbound webhook failed signature verification and must be rejected."""


class RailConfigError(RailError):
    """The rail is misconfigured (missing base URL, key, secret, etc.)."""


class RailUnsupported(RailError):
    """The configured rail does not offer this operation (raised before anything is sent)."""


class UnknownRail(RailError):
    """Requested a rail provider that is not registered."""
