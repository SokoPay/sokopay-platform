"""
Connector interfaces, one per kind of external party.

Every connector carries catalogue metadata (key, display name, regulator, and what is
needed to go live) so operations and compliance can see, in one place, every external
dependency and its status. `available` says whether SokoPay can actually route money
through it today; placeholders are never available, so payments to them are refused
up front rather than failing after a customer has paid.
"""

from __future__ import annotations

import abc

from .types import (
    AccountLookup,
    CrossBorderQuote,
    CrossBorderRecipient,
    CrossBorderRequest,
    Offering,
    PurchaseRequest,
    ApplicationRequest,
    ApplicationResult,
    BankBalance,
    Bundle,
    BundleRequest,
    CardSession,
    CardSessionRequest,
    ConnectorResult,
    DestinationType,
    FinancialProductInfo,
    IdentityResult,
    InboundRemittanceEvent,
    LivenessResult,
    PushToCardRequest,
    TopupRequest,
    TransferDestination,
    TransferRequest,
)
from apps.rails.types import BillRequest


class Connector(abc.ABC):
    key: str = "base"           # registry key, e.g. "ecg"
    display_name: str = ""
    category: str = ""          # biller | telco | transfer | card | insurance | lending
    regulator: str = ""         # who licenses / supervises the counterparty
    go_live: str = ""           # what is needed before this can carry real money
    available: bool = False     # True = SokoPay can route real (or dev-mock) traffic now
    is_mock: bool = False       # True = test/dev double, never for production

    @property
    def status_label(self) -> str:
        if self.is_mock:
            return "mock (dev/test only)"
        return "available" if self.available else "placeholder"


class BillerConnector(Connector):
    category = "biller"

    @abc.abstractmethod
    def lookup_account(self, biller_code: str, account: str) -> AccountLookup: ...

    @abc.abstractmethod
    def pay(self, request: BillRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> ConnectorResult: ...


class TelcoConnector(Connector):
    category = "telco"
    network: str = ""           # mtn | telecel | at

    @abc.abstractmethod
    def list_bundles(self) -> list[Bundle]: ...

    @abc.abstractmethod
    def topup_airtime(self, request: TopupRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def buy_bundle(self, request: BundleRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> ConnectorResult: ...


class TransferConnector(Connector):
    category = "transfer"
    supports: frozenset[DestinationType] = frozenset()

    @abc.abstractmethod
    def name_enquiry(self, destination: TransferDestination) -> AccountLookup: ...

    @abc.abstractmethod
    def send(self, request: TransferRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> ConnectorResult: ...


class CardSchemeConnector(Connector):
    category = "card"

    @abc.abstractmethod
    def create_payment_session(self, request: CardSessionRequest) -> CardSession: ...

    @abc.abstractmethod
    def push_to_card(self, request: PushToCardRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> ConnectorResult: ...


class FinancialProductConnector(Connector):
    # category is "insurance" or "lending" on the concrete class

    @abc.abstractmethod
    def list_products(self) -> list[FinancialProductInfo]: ...

    @abc.abstractmethod
    def submit_application(self, request: ApplicationRequest) -> ApplicationResult: ...

    @abc.abstractmethod
    def application_status(self, partner_ref: str) -> ApplicationResult: ...


class IdentityConnector(Connector):
    category = "identity"

    @abc.abstractmethod
    def verify_ghana_card(self, card_number: str, phone: str) -> IdentityResult: ...

    @abc.abstractmethod
    def verify_liveness(self, selfie: bytes, card_number: str) -> LivenessResult:
        """Is this a live person, and do they match the photo on this Ghana Card?"""


class TrustBankConnector(Connector):
    """Reads the balance of the bank account(s) holding customers' e-money in trust."""

    category = "bank"

    @abc.abstractmethod
    def get_balance(self, account_number: str) -> BankBalance: ...


class RemittanceConnector(Connector):
    """A licensed remittance partner that sends international money into wallets."""

    category = "remittance"

    @abc.abstractmethod
    def verify_and_parse(self, headers: dict, body: bytes) -> InboundRemittanceEvent: ...


class LifestyleConnector(Connector):
    """A partner selling everyday services through SokoPay: event tickets, food ordering.
    Customers pay from their wallet or MoMo; the partner fulfils."""

    # category is "ticketing" or "food" on the concrete class

    @abc.abstractmethod
    def list_offerings(self) -> list[Offering]: ...

    @abc.abstractmethod
    def purchase(self, request: PurchaseRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> ConnectorResult: ...


class CrossBorderConnector(Connector):
    """
    Send money from Ghana to another country through a licensed cross-border partner
    (e.g. Onafriq, Brij). Flow: quote (rate + fee, short-lived) → customer confirms →
    send against the quote → status by webhook / re-query. The partner handles FX and
    pay-out in the destination country.
    """

    category = "cross_border"
    corridors: frozenset[str] = frozenset()     # destination countries (ISO alpha-2)

    @abc.abstractmethod
    def quote(self, send_minor: int, recipient: CrossBorderRecipient) -> CrossBorderQuote: ...

    @abc.abstractmethod
    def send(self, request: CrossBorderRequest) -> ConnectorResult: ...

    @abc.abstractmethod
    def get_status(self, provider_ref: str) -> ConnectorResult: ...
