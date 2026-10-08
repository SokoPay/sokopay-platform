"""
The RailProvider interface.

Every Enhanced PSP partner (Korba, Nsano) and the test `mock` implement this same
contract, so payments/settlement code never depends on a specific partner. SokoPay
runs on ONE partner at a time (settings.RAIL_PROVIDER — Korba or Nsano, never both);
the shared interface just means the choice is a configuration decision, not a rewrite.
"""

from __future__ import annotations

import abc

from .exceptions import RailUnsupported
from .types import (
    AccountLookup,
    BankPayoutRequest,
    BillRequest,
    CollectionRequest,
    PayoutRequest,
    RailResult,
    WebhookEvent,
)


class RailProvider(abc.ABC):
    """Abstract base for a payment rail."""

    #: short name used in settings and account codes, e.g. "korba"
    name: str = "base"

    # --- outbound operations ------------------------------------------------
    @abc.abstractmethod
    def collect(self, request: CollectionRequest) -> RailResult:
        """
        Pull money from a payer. For MoMo this triggers a push prompt on the
        payer's phone and typically returns PENDING; the final result arrives by
        webhook and/or is confirmed by get_status().
        """

    @abc.abstractmethod
    def payout(self, request: PayoutRequest) -> RailResult:
        """Send money to a recipient (merchant settlement, disbursement)."""

    @abc.abstractmethod
    def pay_bill(self, request: BillRequest) -> RailResult:
        """Pay a biller (electricity, water, TV, etc.)."""

    #: Whether this rail can pay into bank accounts. Callers check this BEFORE moving
    #: any money, so an unsupported bank settlement is refused up front.
    supports_bank_payout: bool = False

    def bank_payout(self, request: BankPayoutRequest) -> RailResult:
        """Send money to a bank account. Override where the partner offers it."""
        raise RailUnsupported(f"{self.name} rail does not support bank payouts yet.")

    # --- status & callbacks -------------------------------------------------
    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> RailResult:
        """
        Re-query the rail for the authoritative status of a transaction.

        We ALWAYS call this before acting on a webhook — never trust a callback's
        body alone. This is the control the old codebase lacked.
        """

    @abc.abstractmethod
    def verify_and_parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        """
        Verify a webhook's signature and normalise it to a WebhookEvent.

        Must set signature_ok=False (not raise) on a bad signature so the caller can
        log and reject consistently. Signature comparison must be constant-time.
        """

    # --- reconciliation -----------------------------------------------------
    @abc.abstractmethod
    def settled_transactions(self, date) -> dict:
        """
        Return the rail's record of settled transactions for a date, as
        {provider_ref: amount_in_minor_units}.

        This is the "theirs" side of daily reconciliation (see apps.reconciliation).
        Implemented from the partner's settlement report or statement API.
        """

    # --- optional lookups (not every partner offers them) -------------------
    def lookup_biller_account(self, biller_code: str, account: str) -> AccountLookup:
        """Who owns this meter/smartcard/account? Override where the partner supports it."""
        return AccountLookup(found=False, supported=False,
                             message="Account lookup is not available on this rail yet.")

    def lookup_wallet_name(self, network: str, phone: str) -> AccountLookup:
        """Registered name on a mobile-money wallet. Override where supported."""
        return AccountLookup(found=False, supported=False,
                             message="Wallet name lookup is not available on this rail yet.")
