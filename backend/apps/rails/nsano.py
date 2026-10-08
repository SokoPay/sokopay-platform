"""
Nsano rail adapter (Nsano Ltd — the first BoG Enhanced PSP in Ghana).

Same structure as korba.py: all behaviour comes from HttpPartnerRail (partner.py);
this file is only the Nsano "spec", and every value marked [VERIFY] is an UNCONFIRMED
working value until checked against Nsano's API reference (issued with the partner
agreement and sandbox keys — not public). Then set SPEC_CONFIRMED = True, record
DOCS_VERSION, pass sandbox certification, and ops add "nsano" to
RAIL_CERTIFIED_PARTNERS. See docs/RAILS-INTEGRATION.md.

SokoPay uses ONE partner — Korba or Nsano, never both (no fail-over). Both adapters
exist so the choice is a configuration decision (RAIL_PROVIDER), not a rewrite.

Config: NSANO_BASE_URL, NSANO_CLIENT_ID, NSANO_CLIENT_SECRET, NSANO_WEBHOOK_SECRET,
NSANO_CALLBACK_URL.
"""

from __future__ import annotations

from .partner import HttpPartnerRail
from .signing import HmacRequestSigner
from .types import Network


class NsanoRail(HttpPartnerRail):
    name = "nsano"

    SPEC_CONFIRMED = False
    DOCS_VERSION = ""

    # --- endpoints [VERIFY] ---
    PATH_COLLECT = "/v1/collections"
    PATH_PAYOUT = "/v1/disbursements"
    PATH_BILL = "/v1/bills/payments"
    # Bank payouts stay OFF until Nsano gives us the endpoint. [VERIFY]
    PATH_BANK_PAYOUT = None
    PATH_STATUS = "/v1/transactions/{ref}"
    PATH_WALLET_NAME = "/v1/accounts/name-enquiry"
    PATH_BILL_LOOKUP = "/v1/bills/lookup"
    PATH_SETTLEMENT = "/v1/reports/settlements"

    # --- codes & formats [VERIFY] ---
    NETWORK_CODES = {Network.MTN: "MTN", Network.TELECEL: "TELECEL", Network.AT: "AIRTELTIGO"}
    AMOUNT_AS = "major"
    MAX_REF_LEN = 32

    # --- response fields & status words [VERIFY] ---
    STATUS_FIELD = "data.status"
    MESSAGE_FIELD = "message"
    ERROR_CODE_FIELD = "code"
    REF_FIELD = "data.reference"
    SUCCESS_STATUSES = frozenset({"successful", "success", "completed"})
    FAILED_STATUSES = frozenset({"failed", "declined", "reversed"})
    PENDING_STATUSES = frozenset({"pending", "processing", "initiated"})
    NAME_FIELD = "data.account_name"
    OUTSTANDING_FIELD = "data.amount_due"
    SETTLEMENT_RECORDS_FIELD = "data.records"
    SETTLEMENT_NEXT_PAGE_FIELD = "data.next_page"

    # --- webhooks [VERIFY] ---
    WEBHOOK_SIGNATURE_HEADER = "x-nsano-signature"
    WEBHOOK_TIMESTAMP_HEADER = "x-nsano-timestamp"
    WEBHOOK_SIGNATURE_ENCODING = "hex"

    def make_signer(self, cfg):
        # [VERIFY] scheme name / encoding / what is signed.
        return HmacRequestSigner(cfg["client_id"], cfg["client_secret"], scheme="NSANO-HMAC-SHA256")

    def collect_body(self, req, txn_id):
        return {
            "reference": txn_id,
            "amount": self.amount_out(req.amount),
            "currency": req.amount.currency,
            "channel": self.network_code(req.network),
            "msisdn": req.payer.lstrip("+"),
            "narration": req.narrative[:100],
            "callback_url": self.callback_url(),
        }

    def payout_body(self, req, txn_id):
        return {
            "reference": txn_id,
            "amount": self.amount_out(req.amount),
            "currency": req.amount.currency,
            "channel": self.network_code(req.network),
            "msisdn": req.recipient.lstrip("+"),
            "narration": req.narrative[:100],
            "callback_url": self.callback_url(),
        }

    def bank_payout_body(self, req, txn_id):
        return {
            "reference": txn_id,
            "amount": self.amount_out(req.amount),
            "currency": req.amount.currency,
            "bank_code": req.bank_code,
            "account_number": req.account_number,
            "account_name": req.account_name,
            "narration": req.narrative[:100],
            "callback_url": self.callback_url(),
        }

    def bill_body(self, req, txn_id):
        return {
            "reference": txn_id,
            "amount": self.amount_out(req.amount),
            "currency": req.amount.currency,
            "biller_code": req.biller_code,
            "account_number": req.account,
            "callback_url": self.callback_url(),
        }
