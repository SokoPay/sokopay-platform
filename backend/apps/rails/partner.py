"""
HttpPartnerRail — the shared engine behind the real partner adapters (Korba, Nsano).

What lives HERE (partner-independent, fully tested):
  * transaction ids, request signing, timeouts, no-retry-on-money-POST
  * mapping HTTP outcomes to RailStatus safely (see `_outcome`)
  * status re-query, webhook verification + parsing, settlement-report paging
  * the certification gate (an unverified spec can't move real money)

What lives in the PARTNER SUBCLASS (korba.py / nsano.py) — the "spec":
  * endpoint paths, field names, network/biller codes, status vocabulary,
    signature header names — everything that must come from the partner's API docs.

Safety principles, in order:
  1. Never report SUCCEEDED unless the partner explicitly said a recognised success
     status. Anything unrecognised is UNKNOWN (stay pending, re-query).
  2. Never report FAILED for a money call that may have reached the partner — that
     would refund the customer while the partner may still pay out. Ambiguity → UNKNOWN.
  3. FAILED is used only when the partner definitively rejected the request (4xx with
     a body, or a recognised failure status) or the request provably never left us.

Transaction ids: we send our own id and key everything on it (`provider_ref` is OUR
id). Each operation on a payment gets a distinct, deterministic id —
"<reference>-C" collect, "-P" payout, "-B" bill — so retries reproduce the same id
(the partner de-duplicates) and status lookups never collide.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .base import RailProvider
from .exceptions import RailConfigError, RailError, RailUnsupported
from .http import HttpResponse, PartnerHttpClient, RailTransportError
from .signing import HmacRequestSigner, verify_webhook_signature
from .types import (
    AccountLookup,
    BankPayoutRequest,
    BillRequest,
    CollectionRequest,
    Network,
    PayoutRequest,
    RailResult,
    RailStatus,
    WebhookEvent,
)

logger = logging.getLogger("sokopay.rails")

OP_COLLECT, OP_PAYOUT, OP_BILL = "C", "P", "B"


def dig(data, path: str, default=None):
    """Read a dotted path ("data.transaction.status") from nested dicts."""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


class HttpPartnerRail(RailProvider):
    # ---- the spec: override in each partner adapter ---------------------------------
    #: True only once every value below has been checked against the partner's API
    #: docs (record the docs version in DOCS_VERSION). Production additionally needs
    #: the partner listed in settings.RAIL_CERTIFIED_PARTNERS after a sandbox run.
    SPEC_CONFIRMED = False
    DOCS_VERSION = ""

    PATH_COLLECT = "/collect"
    PATH_PAYOUT = "/payout"
    PATH_BILL = "/bills/pay"
    PATH_BANK_PAYOUT: str | None = None            # None = partner hasn't given us one
    PATH_STATUS = "/transactions/{ref}"            # GET, by OUR transaction id
    PATH_WALLET_NAME = None                        # GET; None = not offered
    PATH_BILL_LOOKUP = None                        # GET; None = not offered
    PATH_SETTLEMENT = None                         # GET report for a date; None = not offered

    NETWORK_CODES = {Network.MTN: "MTN", Network.TELECEL: "TELECEL", Network.AT: "AT"}
    AMOUNT_AS = "major"                            # "major" = "12.50" | "minor" = 1250
    MAX_REF_LEN = 40

    STATUS_FIELD = "status"
    MESSAGE_FIELD = "message"
    ERROR_CODE_FIELD = "error_code"
    REF_FIELD = "transaction_id"                   # where the partner echoes OUR id
    PARTNER_REF_FIELD = "partner_reference"        # the partner's own id (kept for audit)
    SUCCESS_STATUSES: frozenset = frozenset({"success", "successful", "completed"})
    FAILED_STATUSES: frozenset = frozenset({"failed", "declined", "cancelled", "expired"})
    PENDING_STATUSES: frozenset = frozenset({"pending", "processing", "initiated", "queued"})

    WEBHOOK_SIGNATURE_HEADER = "x-signature"
    WEBHOOK_TIMESTAMP_HEADER: str | None = None    # set if the partner signs a timestamp
    WEBHOOK_SIGNATURE_ENCODING = "hex"

    SETTLEMENT_RECORDS_FIELD = "data"
    SETTLEMENT_AMOUNT_FIELD = "amount"
    SETTLEMENT_NEXT_PAGE_FIELD = "next_page"
    SETTLEMENT_MAX_PAGES = 200

    # ---- construction ---------------------------------------------------------------
    def __init__(self, *, config: dict | None = None, transport=None, sleep=None,
                 signer=None) -> None:
        cfg = config if config is not None else getattr(settings, "RAIL_PARTNERS", {}).get(self.name, {})
        missing = [k for k in ("base_url", "client_id", "client_secret", "webhook_secret")
                   if not cfg.get(k)]
        if missing:
            raise RailConfigError(f"{self.name} rail is missing configuration: {', '.join(missing)}")
        self._guard_certified()
        self.cfg = cfg
        sandbox = getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False)
        kwargs = {"sleep": sleep} if sleep else {}
        self.http = PartnerHttpClient(
            partner=self.name, base_url=cfg["base_url"],
            signer=signer or self.make_signer(cfg), transport=transport,
            allow_insecure_http=sandbox, **kwargs,
        )

    def _guard_certified(self) -> None:
        if getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False):
            return                                  # dev / sandbox: experimentation allowed
        certified = set(getattr(settings, "RAIL_CERTIFIED_PARTNERS", []) or [])
        if not self.SPEC_CONFIRMED or self.name not in certified:
            raise RailConfigError(
                f"{self.name} adapter is not certified for live money: SPEC_CONFIRMED="
                f"{self.SPEC_CONFIRMED}, in RAIL_CERTIFIED_PARTNERS={self.name in certified}. "
                "See docs/RAILS-INTEGRATION.md."
            )

    def make_signer(self, cfg: dict):
        return HmacRequestSigner(cfg["client_id"], cfg["client_secret"])

    # ---- ids & amounts --------------------------------------------------------------
    def txn_id(self, reference: str, op: str) -> str:
        raw = f"{reference}-{op}"
        if len(raw) <= self.MAX_REF_LEN:
            return raw
        # Deterministic, so a retry still produces the same id.
        return "SP" + hashlib.sha256(raw.encode()).hexdigest()[: self.MAX_REF_LEN - 2].upper()

    def amount_out(self, money):
        return f"{money.major:.2f}" if self.AMOUNT_AS == "major" else int(money.minor)

    def amount_in(self, value) -> int | None:
        """Partner amount → pesewas. None if it isn't an exact, non-negative amount."""
        if value is None:
            return None
        try:
            if self.AMOUNT_AS == "minor":
                minor = int(str(value))
            else:
                dec = Decimal(str(value))
                if dec != dec.quantize(Decimal("0.01")):
                    return None
                minor = int(dec * 100)
        except (InvalidOperation, ValueError):
            return None
        return minor if minor >= 0 else None

    def network_code(self, network: Network) -> str:
        try:
            return self.NETWORK_CODES[Network(network)]
        except (KeyError, ValueError) as exc:
            raise RailError(f"{self.name} does not support network {network}.") from exc

    # ---- request bodies: override per partner ----------------------------------------
    def callback_url(self) -> str:
        return self.cfg.get("callback_url", "")

    def collect_body(self, req: CollectionRequest, txn_id: str) -> dict:
        return {"transaction_id": txn_id, "amount": self.amount_out(req.amount),
                "currency": req.amount.currency, "network": self.network_code(req.network),
                "phone": req.payer, "description": req.narrative[:100],
                "callback_url": self.callback_url()}

    def payout_body(self, req: PayoutRequest, txn_id: str) -> dict:
        return {"transaction_id": txn_id, "amount": self.amount_out(req.amount),
                "currency": req.amount.currency, "network": self.network_code(req.network),
                "recipient": req.recipient, "description": req.narrative[:100],
                "callback_url": self.callback_url()}

    def bank_payout_body(self, req: BankPayoutRequest, txn_id: str) -> dict:
        return {"transaction_id": txn_id, "amount": self.amount_out(req.amount),
                "currency": req.amount.currency, "bank_code": req.bank_code,
                "account_number": req.account_number, "account_name": req.account_name,
                "description": req.narrative[:100], "callback_url": self.callback_url()}

    def bill_body(self, req: BillRequest, txn_id: str) -> dict:
        return {"transaction_id": txn_id, "amount": self.amount_out(req.amount),
                "currency": req.amount.currency, "biller": req.biller_code,
                "account": req.account, "callback_url": self.callback_url()}

    # ---- outbound operations ----------------------------------------------------------
    def collect(self, request: CollectionRequest) -> RailResult:
        txn = self.txn_id(request.reference, OP_COLLECT)
        return self._money_call(self.PATH_COLLECT, self.collect_body(request, txn), txn,
                                request.idempotency_key)

    def payout(self, request: PayoutRequest) -> RailResult:
        txn = self.txn_id(request.reference, OP_PAYOUT)
        return self._money_call(self.PATH_PAYOUT, self.payout_body(request, txn), txn,
                                request.idempotency_key)

    @property
    def supports_bank_payout(self) -> bool:
        return bool(self.PATH_BANK_PAYOUT)

    def bank_payout(self, request: BankPayoutRequest) -> RailResult:
        if not self.PATH_BANK_PAYOUT:
            raise RailUnsupported(f"{self.name}: bank payout endpoint not configured yet. [VERIFY]")
        txn = self.txn_id(request.reference, OP_PAYOUT)
        return self._money_call(self.PATH_BANK_PAYOUT, self.bank_payout_body(request, txn), txn,
                                request.idempotency_key)

    def pay_bill(self, request: BillRequest) -> RailResult:
        txn = self.txn_id(request.reference, OP_BILL)
        return self._money_call(self.PATH_BILL, self.bill_body(request, txn), txn,
                                request.idempotency_key)

    def _money_call(self, path: str, body: dict, txn: str, idempotency_key: str) -> RailResult:
        try:
            resp = self.http.request("POST", path, json_body=body, idempotency_key=idempotency_key)
        except RailTransportError as exc:
            if not exc.maybe_sent:
                return RailResult(RailStatus.FAILED, txn, message="Payment partner unreachable.",
                                  failure_code="rail_unreachable")
            # It may have gone through. Don't guess: re-query decides.
            return RailResult(RailStatus.UNKNOWN, txn, message="Outcome unknown; will re-query.")
        return self._outcome(resp, txn, initiation=True)

    def _outcome(self, resp: HttpResponse, txn: str, *, initiation: bool) -> RailResult:
        try:
            data = resp.json()
        except RailError:
            data = {}
        message = str(dig(data, self.MESSAGE_FIELD, "") or "")[:255]
        code = str(dig(data, self.ERROR_CODE_FIELD, "") or "")

        if resp.status >= 500 or resp.status in (408, 409):
            # 5xx/408: may have been processed. 409: duplicate id — the original exists.
            return RailResult(RailStatus.UNKNOWN, txn, message=message or f"HTTP {resp.status}", raw=data)
        if resp.status == 429:
            return RailResult(RailStatus.FAILED, txn, message="Partner is busy.",
                              failure_code="rail_busy", raw=data)
        if 400 <= resp.status < 500:
            if not initiation and resp.status == 404:
                return RailResult(RailStatus.UNKNOWN, txn, message="Not found at partner yet.", raw=data)
            return RailResult(RailStatus.FAILED, txn, message=message or f"Rejected (HTTP {resp.status})",
                              failure_code=f"rejected:{code or resp.status}"[:64], raw=data)
        if not 200 <= resp.status < 300:
            return RailResult(RailStatus.UNKNOWN, txn, message=f"HTTP {resp.status}", raw=data)

        # 2xx: trust only a recognised status word. An accepted initiation with no
        # status means "received, in progress".
        raw_status = dig(data, self.STATUS_FIELD)
        status = self.map_status(raw_status)
        if raw_status in (None, "") and initiation:
            status = RailStatus.PENDING
        echoed = dig(data, self.REF_FIELD)
        if echoed not in (None, "") and str(echoed) != txn:
            logger.error("rail=%s echoed a different transaction id; treating as unknown", self.name)
            return RailResult(RailStatus.UNKNOWN, txn, message="Reference mismatch.", raw=data)
        return RailResult(status, txn, message=message,
                          failure_code=(code or "declined") if status == RailStatus.FAILED else "",
                          raw=data)

    def map_status(self, raw) -> RailStatus:
        word = str(raw or "").strip().lower()
        if word in self.SUCCESS_STATUSES:
            return RailStatus.SUCCEEDED
        if word in self.FAILED_STATUSES:
            return RailStatus.FAILED
        if word in self.PENDING_STATUSES:
            return RailStatus.PENDING
        if word:
            logger.warning("rail=%s returned unrecognised status %r — treating as unknown",
                           self.name, word[:32])
        return RailStatus.UNKNOWN

    # ---- status re-query ----------------------------------------------------------------
    def get_status(self, provider_ref: str) -> RailResult:
        try:
            resp = self.http.request("GET", self.PATH_STATUS.format(ref=provider_ref))
        except RailTransportError:
            return RailResult(RailStatus.UNKNOWN, provider_ref, message="Partner unreachable.")
        return self._outcome(resp, provider_ref, initiation=False)

    # ---- webhooks -------------------------------------------------------------------------
    def verify_and_parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        headers = {k.lower(): v for k, v in headers.items()}
        ts = headers.get(self.WEBHOOK_TIMESTAMP_HEADER.lower()) if self.WEBHOOK_TIMESTAMP_HEADER else None
        if self.WEBHOOK_TIMESTAMP_HEADER and ts is None:
            ok = False
        else:
            ok = verify_webhook_signature(
                self.cfg["webhook_secret"], body, headers.get(self.WEBHOOK_SIGNATURE_HEADER.lower(), ""),
                encoding=self.WEBHOOK_SIGNATURE_ENCODING, timestamp=ts,
            )
        try:
            import json
            data = json.loads(body.decode("utf-8")) if body else {}
            data = data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        return WebhookEvent(
            provider_ref=str(dig(data, self.REF_FIELD, "") or "")[:64],
            # Informational only — payments re-queries get_status before acting.
            status=self.map_status(dig(data, self.STATUS_FIELD)),
            signature_ok=ok,
            raw=data if ok else {},          # don't persist attacker-controlled payloads
            message=str(dig(data, self.MESSAGE_FIELD, "") or "")[:255] if ok else "",
        )

    # ---- reconciliation ---------------------------------------------------------------------
    def settled_transactions(self, date) -> dict:
        """
        {our transaction id: amount in pesewas} for transactions the partner settled on
        `date`. Amounts for an id appearing twice are summed (a double settlement then
        shows up as an amount mismatch rather than being hidden).
        """
        if not self.PATH_SETTLEMENT:
            raise RailError(f"{self.name} settlement report endpoint not configured yet. [VERIFY]")
        day = date.isoformat() if isinstance(date, (dt.date, dt.datetime)) else str(date)
        out: dict[str, int] = {}
        page = 1
        for _ in range(self.SETTLEMENT_MAX_PAGES):
            resp = self.http.request("GET", self.PATH_SETTLEMENT, params={"date": day, "page": page})
            if resp.status != 200:
                raise RailError(f"{self.name} settlement report failed: HTTP {resp.status}")
            data = resp.json()
            records = dig(data, self.SETTLEMENT_RECORDS_FIELD, []) or []
            for rec in records if isinstance(records, list) else []:
                if self.map_status(dig(rec, self.STATUS_FIELD)) != RailStatus.SUCCEEDED:
                    continue
                ref = str(dig(rec, self.REF_FIELD, "") or "")
                minor = self.amount_in(dig(rec, self.SETTLEMENT_AMOUNT_FIELD))
                if not ref or minor is None:
                    logger.error("rail=%s settlement record unreadable; skipped (ref=%r)", self.name, ref[:40])
                    continue
                out[ref] = out.get(ref, 0) + minor
            nxt = dig(data, self.SETTLEMENT_NEXT_PAGE_FIELD)
            if not nxt:
                return out
            page += 1
        raise RailError(f"{self.name} settlement report exceeded {self.SETTLEMENT_MAX_PAGES} pages")

    # ---- lookups ---------------------------------------------------------------------------
    def lookup_wallet_name(self, network: str, phone: str) -> AccountLookup:
        if not self.PATH_WALLET_NAME:
            return super().lookup_wallet_name(network, phone)
        return self._lookup(self.PATH_WALLET_NAME,
                            {"network": self.network_code(Network(network)), "phone": phone})

    def lookup_biller_account(self, biller_code: str, account: str) -> AccountLookup:
        if not self.PATH_BILL_LOOKUP:
            return super().lookup_biller_account(biller_code, account)
        return self._lookup(self.PATH_BILL_LOOKUP, {"biller": biller_code, "account": account})

    NAME_FIELD = "account_name"
    OUTSTANDING_FIELD = "amount_due"

    def _lookup(self, path: str, params: dict) -> AccountLookup:
        try:
            resp = self.http.request("GET", path, params=params)
        except RailTransportError:
            return AccountLookup(found=False, message="Couldn't check the name right now. Try again.")
        if resp.status == 404:
            return AccountLookup(found=False, message="Account not found.")
        if resp.status != 200:
            return AccountLookup(found=False, message="Couldn't check the name right now. Try again.")
        data = resp.json()
        name = str(dig(data, self.NAME_FIELD, "") or "").strip()
        return AccountLookup(found=bool(name), account_name=name[:128],
                             outstanding_minor=self.amount_in(dig(data, self.OUTSTANDING_FIELD)),
                             message="" if name else "Account not found.")
