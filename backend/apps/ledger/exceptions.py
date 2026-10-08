class LedgerError(Exception):
    """Base class for all ledger errors."""


class UnbalancedEntry(LedgerError):
    """The postings in a journal entry do not sum to zero."""


class CurrencyMismatch(LedgerError):
    """An entry mixes accounts of different currencies."""


class InvalidEntry(LedgerError):
    """The entry is structurally invalid (too few lines, bad amount, etc.)."""


class InsufficientFunds(LedgerError):
    """A debit/credit would move an account past a balance constraint."""
