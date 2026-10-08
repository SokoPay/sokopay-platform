class MerchantError(Exception):
    """Base class for merchant domain errors."""


class InvalidTransition(MerchantError):
    """An onboarding status change that is not allowed from the current state."""


class SettlementError(MerchantError):
    """A settlement could not be requested or executed."""


class ApprovalError(MerchantError):
    """A maker-checker approval rule was violated (e.g. maker == checker)."""


class RefundError(MerchantError):
    """A refund could not be made (amount, window, balance, method)."""


class DisputeError(MerchantError):
    """A dispute action is not allowed."""
