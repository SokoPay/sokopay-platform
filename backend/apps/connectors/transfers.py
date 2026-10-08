"""
Transfer connectors: sending money from a SokoPay wallet to other institutions in
Ghana (interoperability).

Destinations:
  * momo   — any mobile-money wallet (MTN MoMo, Telecel Cash, AT Money)
  * bank   — any bank account, via GhIPSS Instant Pay (GIP)
  * wallet — another fintech's e-money wallet (G-Money, Zeepay, …)

Routes (chosen per destination by settings.INTEROP_ROUTES):
  * "rail"        — via the Enhanced PSP partner's disbursement API (MoMo). Works today.
  * "ghipss_mmi"  — GhIPSS Mobile Money Interoperability (wallet ↔ wallet). Placeholder.
  * "ghipss_gip"  — GhIPSS Instant Pay (wallet → bank). Placeholder.
  * EMI direct    — mtn_momo, telecel_cash, at_money, gmoney, zeepay. Placeholders.
  * "mock"        — dev/test double.

Licence: sending out of a SokoPay wallet is a DEMI activity (P2P on-net/off-net,
wallet ↔ bank). The licence gate is enforced in apps.wallet, not here.
"""

from __future__ import annotations

import uuid

from apps.rails.registry import get_rail
from apps.rails.types import AccountLookup, Network, PayoutRequest, RailStatus

from .base import TransferConnector
from .billers import _from_rail
from .placeholder import PlaceholderTransfer
from .types import ConnectorResult, DestinationType


class RailTransferConnector(TransferConnector):
    key = "rail"
    display_name = "Via payment partner (Enhanced PSP) — MoMo disbursement"
    regulator = "Bank of Ghana — partner holds an Enhanced PSP licence"
    go_live = "Enhanced PSP agreement with disbursement to all MoMo networks."
    available = True
    supports = frozenset({DestinationType.MOMO})

    def __init__(self, rail_name: str | None = None) -> None:
        self.rail_name = rail_name

    def _rail(self):
        return get_rail(self.rail_name)

    def name_enquiry(self, destination):
        return self._rail().lookup_wallet_name(destination.institution, destination.account)

    def send(self, request):
        dest = request.destination
        return _from_rail(self._rail().payout(PayoutRequest(
            amount=request.amount,
            network=Network(dest.institution),
            recipient=dest.account,
            reference=request.reference,
            narrative=request.narrative,
            idempotency_key=request.idempotency_key,
        )))

    def get_status(self, provider_ref):
        return _from_rail(self._rail().get_status(provider_ref))


class MockTransferConnector(TransferConnector):
    """Dev/test double for every destination type. Accounts ending in 0000 don't exist."""

    key = "mock"
    display_name = "Mock transfer connector"
    regulator = "—"
    go_live = "Never used in production."
    available = True
    is_mock = True
    supports = frozenset(DestinationType)

    _state: dict[str, RailStatus] = {}
    _next_status: RailStatus = RailStatus.SUCCEEDED
    _fail_accounts: set[str] = set()

    @classmethod
    def reset(cls) -> None:
        cls._state.clear()
        cls._next_status = RailStatus.SUCCEEDED
        cls._fail_accounts.clear()

    @classmethod
    def script(cls, status: RailStatus) -> None:
        """Set the status the next send() returns (SUCCEEDED, PENDING or FAILED)."""
        cls._next_status = status

    @classmethod
    def fail_for(cls, account: str) -> None:
        """Make sends to one specific account fail (mixed outcomes in one bulk batch)."""
        cls._fail_accounts.add(account)

    @classmethod
    def drive(cls, provider_ref: str, status: RailStatus) -> None:
        cls._state[provider_ref] = status

    def name_enquiry(self, destination):
        if destination.account.endswith("0000"):
            return AccountLookup(found=False, message="Account not found.")
        return AccountLookup(found=True, account_name=f"TEST RECIPIENT {destination.account[-4:]}")

    def send(self, request):
        ref = f"XFER-{uuid.uuid4().hex[:12]}"
        status = (RailStatus.FAILED if request.destination.account in self._fail_accounts
                  else self._next_status)
        self._state[ref] = status
        return ConnectorResult(status=status, provider_ref=ref,
                               failure_code="destination_declined" if status == RailStatus.FAILED else "")

    def get_status(self, provider_ref):
        return ConnectorResult(status=self._state.get(provider_ref, RailStatus.UNKNOWN),
                               provider_ref=provider_ref)


# --- GhIPSS (national switch) placeholders --------------------------------------
class GhipssMmiConnector(PlaceholderTransfer):
    key = "ghipss_mmi"
    display_name = "GhIPSS Mobile Money Interoperability (MMI)"
    regulator = "Bank of Ghana (GhIPSS is BoG's payments subsidiary)"
    go_live = ("Participation in GhIPSS MMI — directly (Enhanced PSP/DEMI with BoG "
               "approval) or through a sponsoring participant; MMI API specs, "
               "certification and settlement account. [VERIFY]")
    supports = frozenset({DestinationType.MOMO, DestinationType.WALLET})


class GhipssGipConnector(PlaceholderTransfer):
    key = "ghipss_gip"
    display_name = "GhIPSS Instant Pay (GIP) — wallet to bank"
    regulator = "Bank of Ghana (GhIPSS)"
    go_live = ("GIP participation directly or via a sponsor bank; name-enquiry and "
               "funds-transfer API specs; certification; settlement account. [VERIFY]")
    supports = frozenset({DestinationType.BANK})


# --- EMI / wallet providers in Ghana (direct-integration placeholders) ------------
class MtnMomoConnector(PlaceholderTransfer):
    key = "mtn_momo"
    display_name = "MTN Mobile Money (direct)"
    regulator = "Bank of Ghana (licensed EMI)"
    go_live = "Commercial agreement with MTN MoMo; MoMo API (disbursement) keys. [VERIFY]"
    supports = frozenset({DestinationType.MOMO})


class TelecelCashConnector(PlaceholderTransfer):
    key = "telecel_cash"
    display_name = "Telecel Cash (direct)"
    regulator = "Bank of Ghana (licensed EMI)"
    go_live = "Commercial agreement with Telecel Cash; disbursement API and keys. [VERIFY]"
    supports = frozenset({DestinationType.MOMO})


class AtMoneyConnector(PlaceholderTransfer):
    key = "at_money"
    display_name = "AT Money (formerly AirtelTigo Money) (direct)"
    regulator = "Bank of Ghana (licensed EMI)"
    go_live = "Commercial agreement with AT Money; disbursement API and keys. [VERIFY]"
    supports = frozenset({DestinationType.MOMO})


class GMoneyConnector(PlaceholderTransfer):
    key = "gmoney"
    display_name = "G-Money (GCB Bank)"
    regulator = "Bank of Ghana"
    go_live = "Partnership with GCB Bank for G-Money; API access and keys. [VERIFY]"
    supports = frozenset({DestinationType.WALLET})


class ZeepayConnector(PlaceholderTransfer):
    key = "zeepay"
    display_name = "Zeepay"
    regulator = "Bank of Ghana (licensed DEMI)"
    go_live = "Partnership with Zeepay; wallet credit API and keys. [VERIFY]"
    supports = frozenset({DestinationType.WALLET})
