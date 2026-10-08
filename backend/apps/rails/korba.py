"""
Korba rail adapter (Halges Financial Technologies — a BoG Enhanced PSP).

All behaviour — signing, timeouts, safe status mapping, webhooks, settlement paging,
the certification gate — comes from HttpPartnerRail (partner.py) and is tested.

THIS FILE IS THE KORBA "SPEC": the endpoint paths, field names, codes and status words
below are UNCONFIRMED working values. Korba's API reference is issued with the
merchant/partner agreement and sandbox keys; it is not public. When it arrives:

  1. Replace every value marked [VERIFY] with the documented one, and adjust the
     *_body() methods / make_signer() if the documented shapes differ.
  2. Record the docs version in DOCS_VERSION and set SPEC_CONFIRMED = True.
  3. Run the sandbox certification (docs/RAILS-INTEGRATION.md); then ops add "korba"
     to RAIL_CERTIFIED_PARTNERS. Production refuses to use the adapter before that.

Config (env, never hardcoded): KORBA_BASE_URL, KORBA_CLIENT_ID, KORBA_CLIENT_SECRET,
KORBA_WEBHOOK_SECRET, KORBA_CALLBACK_URL.
"""

from __future__ import annotations

from .partner import HttpPartnerRail
from .signing import HmacRequestSigner
from .types import Network


class KorbaRail(HttpPartnerRail):
    name = "korba"

    SPEC_CONFIRMED = False                     # flip only after checking against the docs
    DOCS_VERSION = ""                          # e.g. "Korba Collect API v2, 2026-11"

    # --- endpoints [VERIFY] ---
    PATH_COLLECT = "/collect/"
    PATH_PAYOUT = "/disburse/"
    PATH_BILL = "/bill/pay/"
    # Bank payouts (merchant settlement to a bank account) stay OFF until Korba gives us
    # the endpoint; bank settlements are refused up front until then. [VERIFY]
    PATH_BANK_PAYOUT = None
    PATH_STATUS = "/transaction/status/{ref}/"
    PATH_WALLET_NAME = "/momo/name-enquiry/"
    PATH_BILL_LOOKUP = "/bill/lookup/"
    PATH_SETTLEMENT = "/reports/settlement/"

    # --- codes & formats [VERIFY] ---
    NETWORK_CODES = {Network.MTN: "MTN", Network.TELECEL: "VOD", Network.AT: "AIR"}
    AMOUNT_AS = "major"
    MAX_REF_LEN = 40

    # --- response fields & status words [VERIFY] ---
    STATUS_FIELD = "status"
    MESSAGE_FIELD = "message"
    ERROR_CODE_FIELD = "error_code"
    REF_FIELD = "transaction_id"
    SUCCESS_STATUSES = frozenset({"success", "successful"})
    FAILED_STATUSES = frozenset({"failed", "declined", "cancelled"})
    PENDING_STATUSES = frozenset({"pending", "processing"})

    # --- webhooks [VERIFY] ---
    WEBHOOK_SIGNATURE_HEADER = "x-korba-signature"
    WEBHOOK_TIMESTAMP_HEADER = None
    WEBHOOK_SIGNATURE_ENCODING = "hex"

    def make_signer(self, cfg):
        # [VERIFY] scheme name / encoding / what is signed.
        return HmacRequestSigner(cfg["client_id"], cfg["client_secret"], scheme="HMAC")

    # Request bodies [VERIFY field names]
    def collect_body(self, req, txn_id):
        return {
            "transaction_id": txn_id,
            "amount": self.amount_out(req.amount),
            "network_code": self.network_code(req.network),
            "customer_number": req.payer.lstrip("+"),
            "description": req.narrative[:100],
            "callback_url": self.callback_url(),
            "client_id": self.cfg["client_id"],
        }

    def payout_body(self, req, txn_id):
        return {
            "transaction_id": txn_id,
            "amount": self.amount_out(req.amount),
            "network_code": self.network_code(req.network),
            "customer_number": req.recipient.lstrip("+"),
            "description": req.narrative[:100],
            "callback_url": self.callback_url(),
            "client_id": self.cfg["client_id"],
        }

    def bank_payout_body(self, req, txn_id):
        return {
            "transaction_id": txn_id,
            "amount": self.amount_out(req.amount),
            "bank_code": req.bank_code,
            "account_number": req.account_number,
            "account_name": req.account_name,
            "description": req.narrative[:100],
            "callback_url": self.callback_url(),
            "client_id": self.cfg["client_id"],
        }

    def bill_body(self, req, txn_id):
        return {
            "transaction_id": txn_id,
            "amount": self.amount_out(req.amount),
            "service_code": req.biller_code,
            "account_number": req.account,
            "callback_url": self.callback_url(),
            "client_id": self.cfg["client_id"],
        }
