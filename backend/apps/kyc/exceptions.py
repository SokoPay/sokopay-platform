class KycError(Exception):
    """Base for KYC/limit refusals. Messages are customer-safe (shown in the app)."""


class LimitExceeded(KycError):
    """The operation would breach the customer's tier limits."""


class WalletFrozen(KycError):
    """Compliance has put the wallet on hold; money cannot leave it."""


class VerificationFailed(KycError):
    """Identity verification did not succeed."""
