"""
Shared value types for the rail layer.

A "rail" is an Enhanced PSP partner (Korba, Nsano) or scheme we connect to in order
to actually move money on the mobile-money and card networks. Every rail speaks a
different wire protocol; these types are the single, normalised shape the rest of
SokoPay works with, so no partner-specific detail leaks into payments/settlement code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from apps.common.money import Money


class Network(str, Enum):
    """Payment networks SokoPay supports. New-brand names only."""

    MTN = "mtn"            # MTN MoMo
    TELECEL = "telecel"    # Telecel Cash (formerly Vodafone Cash)
    AT = "at"              # AT Money (formerly AirtelTigo)
    CARD = "card"          # Visa / Mastercard / GH-Link (via partner-hosted fields)

    def __str__(self) -> str:
        return self.value


class RailStatus(str, Enum):
    """Normalised transaction status returned by any rail."""

    PENDING = "pending"      # initiated; awaiting customer approval / processing
    SUCCEEDED = "succeeded"  # confirmed complete
    FAILED = "failed"        # confirmed failed (declined, insufficient, timeout)
    UNKNOWN = "unknown"      # rail could not tell us (treat as still pending; re-query)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CollectionRequest:
    """Ask the rail to pull money from a payer (a 'collect' / debit)."""

    amount: Money
    network: Network
    payer: str                 # E.164 phone for MoMo; opaque token for card
    reference: str             # our unique reference (SP-...)
    narrative: str
    idempotency_key: str


@dataclass(frozen=True)
class PayoutRequest:
    """Ask the rail to send money to a recipient (settlement, disbursement)."""

    amount: Money
    network: Network
    recipient: str             # E.164 phone (MoMo) or account number (bank)
    reference: str
    narrative: str
    idempotency_key: str


@dataclass(frozen=True)
class BankPayoutRequest:
    """Send money to a bank account (e.g. a merchant settlement to their bank)."""

    amount: Money
    bank_code: str             # the partner's code for the destination bank
    account_number: str
    account_name: str          # name on the account, for the bank's name check
    reference: str
    narrative: str
    idempotency_key: str


@dataclass(frozen=True)
class BillRequest:
    """Ask the rail to pay a biller (after we have collected from the customer)."""

    amount: Money
    biller_code: str           # rail/biller-specific code, e.g. "ECG_PREPAID"
    account: str               # meter/account/smartcard number
    reference: str
    idempotency_key: str


@dataclass(frozen=True)
class RailResult:
    """The outcome of a rail operation."""

    status: RailStatus
    provider_ref: str                     # the rail's own reference for this transaction
    message: str = ""
    failure_code: str = ""                # normalised code when status == FAILED
    raw: dict = field(default_factory=dict)  # the untouched provider payload, for audit


@dataclass(frozen=True)
class WebhookEvent:
    """A normalised inbound callback from a rail."""

    provider_ref: str
    status: RailStatus
    signature_ok: bool
    raw: dict = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class AccountLookup:
    """
    Result of a name/account enquiry — "who owns this meter / wallet / account?".

    Shown to the customer before they pay or transfer, so they can confirm the
    recipient. This is one of the main defences against paying the wrong person.
    """

    found: bool
    account_name: str = ""
    outstanding_minor: int | None = None   # amount owed, where a biller reports it
    message: str = ""
    supported: bool = True                 # False = this route can't do lookups yet
