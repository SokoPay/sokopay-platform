"""
MockRail — a fully in-process rail for local development and tests.

It lets tests script exactly the awkward situations a real rail throws at us:
pending-then-success, duplicate callbacks, callbacks that disagree with a re-query,
failures, and bad signatures. See apps/rails/tests and the QA plan (../../docs).

State lives in a class-level dict keyed by provider_ref. `drive()` moves a
transaction to a new status, standing in for "the customer approved on their phone"
or "the biller accepted". This is test/dev scaffolding only and never ships enabled
in production (RAIL_PROVIDER would be korba/nsano there).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import uuid

from django.conf import settings
from django.utils import timezone

from .base import RailProvider
from .types import (
    AccountLookup,
    BankPayoutRequest,
    BillRequest,
    CollectionRequest,
    PayoutRequest,
    RailResult,
    RailStatus,
    WebhookEvent,
)


class MockRail(RailProvider):
    name = "mock"

    # provider_ref -> RailStatus. Class-level so it survives within a test/process.
    _state: dict[str, RailStatus] = {}
    # provider_ref -> (kind, amount_minor, date). Lets the mock produce a settlement
    # report for reconciliation, the way a real partner's statement API would.
    _records: dict[str, tuple[str, int, dt.date]] = {}
    # The status pay_bill() returns. Tests flip this to SUCCEEDED/FAILED to exercise
    # the biller-success and biller-failure (auto-refund) paths.
    _bill_status: RailStatus = RailStatus.SUCCEEDED

    def _secret(self) -> bytes:
        return (getattr(settings, "RAIL_WEBHOOK_SECRET", "") or "mock-secret").encode()

    @classmethod
    def _record(cls, ref: str, kind: str, amount_minor: int) -> None:
        cls._records[ref] = (kind, amount_minor, timezone.now().date())

    # --- test/dev helpers ---------------------------------------------------
    @classmethod
    def reset(cls) -> None:
        cls._state.clear()
        cls._records.clear()

    @classmethod
    def add_settled(cls, ref: str, amount_minor: int, date: dt.date | None = None) -> None:
        """Inject a settled collection the rail knows about (e.g. one we never recorded)."""
        cls._records[ref] = ("collect", amount_minor, date or timezone.now().date())
        cls._state[ref] = RailStatus.SUCCEEDED

    @classmethod
    def drive(cls, provider_ref: str, status: RailStatus) -> None:
        """Simulate the rail moving a transaction to `status` (customer approved, etc.)."""
        cls._state[provider_ref] = status

    @classmethod
    def set_bill_status(cls, status: RailStatus) -> None:
        """Set the status pay_bill() will return (tests exercise success and failure)."""
        cls._bill_status = status

    def sign(self, body: bytes) -> str:
        """Produce the signature a real rail would send, for use in tests."""
        return hmac.new(self._secret(), body, hashlib.sha256).hexdigest()

    # --- outbound -----------------------------------------------------------
    def collect(self, request: CollectionRequest) -> RailResult:
        ref = f"MOCK-{uuid.uuid4().hex[:16]}"
        # A MoMo collection starts PENDING; the customer must approve on their phone.
        self._state[ref] = RailStatus.PENDING
        self._record(ref, "collect", request.amount.minor)
        return RailResult(status=RailStatus.PENDING, provider_ref=ref, message="prompt sent")

    def payout(self, request: PayoutRequest) -> RailResult:
        ref = f"MOCK-{uuid.uuid4().hex[:16]}"
        self._state[ref] = RailStatus.PENDING
        self._record(ref, "payout", request.amount.minor)
        return RailResult(status=RailStatus.PENDING, provider_ref=ref, message="payout queued")

    supports_bank_payout = True

    def bank_payout(self, request: BankPayoutRequest) -> RailResult:
        ref = f"MOCK-{uuid.uuid4().hex[:16]}"
        self._state[ref] = RailStatus.PENDING
        self._record(ref, "bank_payout", request.amount.minor)
        return RailResult(status=RailStatus.PENDING, provider_ref=ref, message="bank payout queued")

    def pay_bill(self, request: BillRequest) -> RailResult:
        ref = f"MOCK-{uuid.uuid4().hex[:16]}"
        # Bills resolve synchronously in the mock, with the scripted status.
        self._state[ref] = self._bill_status
        self._record(ref, "bill", request.amount.minor)
        msg = "bill paid" if self._bill_status == RailStatus.SUCCEEDED else "biller declined"
        return RailResult(status=self._bill_status, provider_ref=ref, message=msg)

    # --- status & callbacks -------------------------------------------------
    def get_status(self, provider_ref: str) -> RailResult:
        status = self._state.get(provider_ref, RailStatus.UNKNOWN)
        return RailResult(status=status, provider_ref=provider_ref)

    def verify_and_parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        # Headers are normalised to lowercase keys by the caller.
        sent = headers.get("x-mock-signature", "")
        expected = self.sign(body)
        signature_ok = hmac.compare_digest(sent, expected)  # constant-time

        try:
            payload = json.loads(body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}

        status_raw = str(payload.get("status", "unknown")).lower()
        try:
            status = RailStatus(status_raw)
        except ValueError:
            status = RailStatus.UNKNOWN

        return WebhookEvent(
            provider_ref=payload.get("provider_ref", ""),
            status=status,
            signature_ok=signature_ok,
            raw=payload,
            message=payload.get("message", ""),
        )

    # --- lookups (deterministic test data) ----------------------------------
    # Convention for tests/dev: an account or phone ending in "0000" does not exist.
    def lookup_biller_account(self, biller_code: str, account: str) -> AccountLookup:
        if account.endswith("0000"):
            return AccountLookup(found=False, message="Account not found.")
        return AccountLookup(found=True, account_name=f"TEST CUSTOMER {account[-4:]}",
                             outstanding_minor=None)

    def lookup_wallet_name(self, network: str, phone: str) -> AccountLookup:
        if phone.endswith("0000"):
            return AccountLookup(found=False, message="Wallet not found.")
        return AccountLookup(found=True, account_name=f"TEST WALLET {phone[-4:]}")

    # --- reconciliation -----------------------------------------------------
    def settled_transactions(self, date: dt.date) -> dict[str, int]:
        """
        The mock's settlement report: successful *collections* on `date`. Outbound
        legs (bill deliveries, payouts) are excluded; they belong to a separate
        payout reconciliation.
        """
        return {
            ref: amount
            for ref, (kind, amount, day) in self._records.items()
            if kind == "collect" and day == date and self._state.get(ref) == RailStatus.SUCCEEDED
        }
