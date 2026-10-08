"""
Value types shared by all connectors.

A *connector* is SokoPay's adapter to one external party we don't route through the
payment rail: a biller (ECG, Ghana Water, DStv…), a telco (airtime/data), another
fintech or the GhIPSS switch (interoperable transfers), a card scheme (Visa,
Mastercard, gh-link), or a financial-services partner (insurer, lender).

Statuses reuse the rail's RailStatus so the rest of the system has one vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from apps.common.money import Money
from apps.rails.types import AccountLookup, RailStatus  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class ConnectorResult:
    """Outcome of an operation with an external party."""

    status: RailStatus
    provider_ref: str = ""
    message: str = ""
    failure_code: str = ""
    token: str = ""            # e.g. an ECG prepaid token, delivered to the customer
    raw: dict = field(default_factory=dict)


# --- telcos (airtime & data) --------------------------------------------------
@dataclass(frozen=True)
class Bundle:
    code: str
    name: str
    network: str
    price_minor: int
    volume: str           # e.g. "1.5 GB"
    validity: str         # e.g. "7 days"
    is_sample: bool = False  # True = placeholder catalogue, not the telco's live list


@dataclass(frozen=True)
class TopupRequest:
    network: str
    phone: str            # E.164 number being credited
    amount: Money
    reference: str
    idempotency_key: str


@dataclass(frozen=True)
class BundleRequest:
    phone: str
    bundle: Bundle
    reference: str
    idempotency_key: str


# --- interoperable transfers ---------------------------------------------------
class DestinationType(str, Enum):
    MOMO = "momo"       # a mobile-money wallet on any network (MTN, Telecel, AT)
    BANK = "bank"       # a bank account (via GhIPSS Instant Pay)
    WALLET = "wallet"   # another fintech's e-money wallet (e.g. G-Money, Zeepay)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class TransferDestination:
    type: DestinationType
    institution: str      # network code (mtn), bank code, or provider key (zeepay)
    account: str          # phone for wallets, account number for banks


@dataclass(frozen=True)
class TransferRequest:
    amount: Money
    destination: TransferDestination
    sender_name: str
    reference: str
    narrative: str
    idempotency_key: str


# --- card schemes -------------------------------------------------------------
@dataclass(frozen=True)
class CardSessionRequest:
    """Start a hosted card payment. SokoPay never sees the card number (PAN)."""

    amount: Money
    reference: str
    return_url: str
    idempotency_key: str


@dataclass(frozen=True)
class CardSession:
    session_id: str
    redirect_url: str     # the scheme/acquirer-hosted page the customer is sent to


@dataclass(frozen=True)
class PushToCardRequest:
    amount: Money
    card_token: str       # a token from the scheme/acquirer — never a raw card number
    reference: str
    idempotency_key: str


# --- financial services (insurance, lending) -----------------------------------
@dataclass(frozen=True)
class FinancialProductInfo:
    code: str
    name: str
    category: str         # "insurance" | "lending"
    summary: str
    min_minor: int | None = None
    max_minor: int | None = None


@dataclass(frozen=True)
class ApplicationRequest:
    product_code: str
    applicant_phone: str
    applicant_name: str
    amount_minor: int | None
    reference: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ApplicationResult:
    status: str           # "submitted" | "pending" | "approved" | "declined"
    partner_ref: str = ""
    message: str = ""


# --- identity (KYC) -------------------------------------------------------------
@dataclass(frozen=True)
class IdentityResult:
    verified: bool
    full_name: str = ""   # name on the national ID record
    message: str = ""


@dataclass(frozen=True)
class LivenessResult:
    """Outcome of a live-selfie check against the Ghana Card photo."""

    passed: bool
    match_score: float = 0.0   # 0–1 face-match confidence from the provider
    provider_ref: str = ""
    message: str = ""


# --- trust bank (e-money safeguarding) -------------------------------------------
@dataclass(frozen=True)
class BankBalance:
    balance_minor: int
    as_of: str            # ISO timestamp from the bank


# --- inbound remittance ----------------------------------------------------------
@dataclass(frozen=True)
class InboundRemittanceEvent:
    partner_ref: str
    recipient_phone: str
    amount_minor: int
    currency: str
    sender_name: str = ""
    sender_country: str = ""
    signature_ok: bool = False


# --- lifestyle (tickets, food) ------------------------------------------------------
@dataclass(frozen=True)
class Offering:
    """Something a lifestyle partner sells: an event ticket type, a menu item…"""

    code: str
    name: str
    price_minor: int
    description: str = ""


@dataclass(frozen=True)
class PurchaseRequest:
    offering_code: str
    quantity: int
    buyer_phone: str
    amount: Money
    reference: str
    idempotency_key: str


# --- cross-border (outbound, via licensed partners) -----------------------------------
@dataclass(frozen=True)
class CrossBorderQuote:
    """What the recipient gets for a GHS amount, before the customer commits."""

    quote_id: str
    send_minor: int               # GHS pesewas debited from the customer (incl. fee)
    fee_minor: int
    receive_amount: str           # decimal string in the destination currency
    receive_currency: str         # e.g. NGN, KES, XOF
    rate: str                     # decimal string
    expires_at: str               # ISO timestamp — quotes are short-lived


@dataclass(frozen=True)
class CrossBorderRecipient:
    country: str                  # ISO-3166 alpha-2, e.g. NG
    method: str                   # "mobile_money" | "bank"
    account: str                  # phone or account number
    institution: str              # network / bank code in that country
    name: str


@dataclass(frozen=True)
class CrossBorderRequest:
    quote_id: str
    recipient: CrossBorderRecipient
    sender_name: str
    purpose: str                  # needed for FX/AML reporting
    reference: str
    idempotency_key: str
