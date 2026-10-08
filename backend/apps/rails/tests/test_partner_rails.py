"""
Korba / Nsano adapters, exercised through a fake transport (no network).

These test the partner-independent guarantees — signing, timeouts → UNKNOWN, safe
status mapping, webhook verification, settlement paging, the certification gate —
for BOTH adapters. Field names come from each adapter's spec, so when a spec is updated
from the partner's docs these tests keep proving the guarantees still hold. Recorded
sandbox responses get added as contract fixtures during certification.
"""

import datetime as dt
import hashlib
import hmac
import json
import time

import pytest

from apps.common.money import Money
from apps.rails.exceptions import RailConfigError, RailError
from apps.rails.http import HttpResponse, RailTransportError
from apps.rails.korba import KorbaRail
from apps.rails.nsano import NsanoRail
from apps.rails.partner import dig
from apps.rails.types import BillRequest, CollectionRequest, Network, PayoutRequest, RailStatus

CFG = {"base_url": "https://sandbox.partner.test/api", "client_id": "cid-123",
       "client_secret": "s3cret", "webhook_secret": "whsec", "callback_url": "https://api.test/cb"}


class FakeTransport:
    """Records requests; replies from a queue of HttpResponse or exceptions."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body else None, "timeout": timeout})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok(rail_cls, status=None, ref=None, **extra) -> HttpResponse:
    """Build a 200 response in the partner's own field layout (from its spec)."""
    data: dict = {}

    def put(path, value):
        cur = data
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    if status is not None:
        put(rail_cls.STATUS_FIELD, status)
    if ref is not None:
        put(rail_cls.REF_FIELD, ref)
    for k, v in extra.items():
        put(k, v)
    return HttpResponse(200, json.dumps(data).encode())


def make(rail_cls, *replies):
    t = FakeTransport(*replies)
    return rail_cls(config=CFG, transport=t, sleep=lambda s: None), t


def collect_req(ref="SP-ABC123"):
    return CollectionRequest(amount=Money(1250, "GHS"), network=Network.MTN, payer="+233244058519",
                             reference=ref, narrative="ECG 0123", idempotency_key="collect-init:1")


RAILS = [KorbaRail, NsanoRail]


@pytest.fixture(autouse=True)
def _dev(settings):
    settings.ALLOW_MOCK_INTEGRATIONS = True   # sandbox mode for most tests
    settings.RAIL_CERTIFIED_PARTNERS = []


# --- certification gate & config ---------------------------------------------------------
@pytest.mark.parametrize("rail_cls", RAILS)
def test_unconfirmed_spec_cannot_move_live_money(settings, rail_cls):
    settings.ALLOW_MOCK_INTEGRATIONS = False
    with pytest.raises(RailConfigError, match="not certified"):
        rail_cls(config=CFG, transport=FakeTransport())
    settings.RAIL_CERTIFIED_PARTNERS = [rail_cls.name]          # ops list alone isn't enough
    with pytest.raises(RailConfigError):
        rail_cls(config=CFG, transport=FakeTransport())


@pytest.mark.parametrize("rail_cls", RAILS)
def test_certified_and_confirmed_spec_starts(settings, monkeypatch, rail_cls):
    settings.ALLOW_MOCK_INTEGRATIONS = False
    settings.RAIL_CERTIFIED_PARTNERS = [rail_cls.name]
    monkeypatch.setattr(rail_cls, "SPEC_CONFIRMED", True)
    assert rail_cls(config=CFG, transport=FakeTransport()).name == rail_cls.name


@pytest.mark.parametrize("rail_cls", RAILS)
def test_config_and_https_required(settings, rail_cls):
    with pytest.raises(RailConfigError, match="client_secret"):
        rail_cls(config={**CFG, "client_secret": ""}, transport=FakeTransport())
    settings.ALLOW_MOCK_INTEGRATIONS = False
    settings.RAIL_CERTIFIED_PARTNERS = [rail_cls.name]
    rail_cls.SPEC_CONFIRMED = True
    try:
        with pytest.raises(RailConfigError, match="https"):
            rail_cls(config={**CFG, "base_url": "http://plain.test"}, transport=FakeTransport())
    finally:
        rail_cls.SPEC_CONFIRMED = False


# --- outbound requests -------------------------------------------------------------------
@pytest.mark.parametrize("rail_cls", RAILS)
def test_collect_sends_signed_request_with_our_txn_id(rail_cls):
    rail, t = make(rail_cls, ok(rail_cls, status=sorted(rail_cls.PENDING_STATUSES)[0]))
    result = rail.collect(collect_req())
    assert result.status == RailStatus.PENDING and result.provider_ref == "SP-ABC123-C"

    call = t.calls[0]
    assert call["method"] == "POST" and call["url"] == CFG["base_url"] + rail_cls.PATH_COLLECT
    assert call["timeout"] == (5, 30)
    assert call["headers"]["Idempotency-Key"] == "collect-init:1"
    body_text = json.dumps(call["body"])
    assert "SP-ABC123-C" in body_text and "12.50" in body_text
    assert rail_cls.NETWORK_CODES[Network.MTN] in body_text
    assert CFG["client_secret"] not in json.dumps(call)           # secret never sent

    # Signature verifies with the documented canonical layout.
    h = call["headers"]
    signer = rail.http.signer
    canonical = signer.canonical("POST", "/api" + rail_cls.PATH_COLLECT, h["X-Timestamp"], h["X-Nonce"],
                                 json.dumps(call["body"], separators=(",", ":"), sort_keys=True).encode())
    expected = hmac.new(b"s3cret", canonical, hashlib.sha256).hexdigest()
    assert h["Authorization"].endswith(f"cid-123:{expected}")


@pytest.mark.parametrize("rail_cls", RAILS)
def test_each_operation_gets_a_distinct_deterministic_id(rail_cls):
    rail, _ = make(rail_cls)
    assert rail.txn_id("SP-X", "C") != rail.txn_id("SP-X", "B")
    long_ref = "SETTLE-" + "f" * 60
    a, b = rail.txn_id(long_ref, "P"), rail.txn_id(long_ref, "P")
    assert a == b and len(a) <= rail_cls.MAX_REF_LEN


@pytest.mark.parametrize("rail_cls", RAILS)
def test_payout_and_bill_hit_their_endpoints(rail_cls):
    rail, t = make(rail_cls, ok(rail_cls), ok(rail_cls))
    rail.payout(PayoutRequest(Money(5000, "GHS"), Network.TELECEL, "+233201234567", "SETTLE-1", "x", "k1"))
    rail.pay_bill(BillRequest(Money(2000, "GHS"), "ECG_PREPAID", "0123456789", "SP-9", "k2"))
    assert t.calls[0]["url"].endswith(rail_cls.PATH_PAYOUT) and "SETTLE-1-P" in json.dumps(t.calls[0]["body"])
    assert t.calls[1]["url"].endswith(rail_cls.PATH_BILL) and "ECG_PREPAID" in json.dumps(t.calls[1]["body"])


@pytest.mark.parametrize("rail_cls", RAILS)
def test_card_network_is_refused_not_guessed(rail_cls):
    rail, t = make(rail_cls)
    with pytest.raises(RailError):
        rail.payout(PayoutRequest(Money(100, "GHS"), Network.CARD, "123", "R", "x", "k"))
    assert t.calls == []


# --- outcome safety -------------------------------------------------------------------------
@pytest.mark.parametrize("rail_cls", RAILS)
@pytest.mark.parametrize("reply, expected", [
    (RailTransportError("ConnectTimeout", maybe_sent=False), RailStatus.FAILED),   # never left us
    (RailTransportError("ReadTimeout", maybe_sent=True), RailStatus.UNKNOWN),      # may have paid
    (HttpResponse(500, b"oops"), RailStatus.UNKNOWN),
    (HttpResponse(502, b""), RailStatus.UNKNOWN),
    (HttpResponse(409, b'{"message":"duplicate"}'), RailStatus.UNKNOWN),
    (HttpResponse(429, b""), RailStatus.FAILED),
    (HttpResponse(400, b'{"message":"bad msisdn"}'), RailStatus.FAILED),
    (HttpResponse(200, b"<html>not json</html>"), RailStatus.PENDING),             # accepted, no status
])
def test_money_call_outcomes(rail_cls, reply, expected):
    rail, t = make(rail_cls, reply)
    assert rail.collect(collect_req()).status == expected
    assert len(t.calls) == 1                                       # money POSTs never retried


@pytest.mark.parametrize("rail_cls", RAILS)
def test_unrecognised_status_is_never_success(rail_cls):
    rail, _ = make(rail_cls, ok(rail_cls, status="SUCCESS_MAYBE"), ok(rail_cls, status="approved?"))
    assert rail.collect(collect_req()).status == RailStatus.UNKNOWN
    assert rail.get_status("SP-ABC123-C").status == RailStatus.UNKNOWN


@pytest.mark.parametrize("rail_cls", RAILS)
def test_mismatched_echoed_reference_is_unknown(rail_cls):
    success = sorted(rail_cls.SUCCESS_STATUSES)[0]
    rail, _ = make(rail_cls, ok(rail_cls, status=success, ref="SOMEONE-ELSE"))
    assert rail.get_status("SP-ABC123-C").status == RailStatus.UNKNOWN


@pytest.mark.parametrize("rail_cls", RAILS)
def test_get_status_maps_and_retries_reads(rail_cls):
    success = sorted(rail_cls.SUCCESS_STATUSES)[0]
    failed = sorted(rail_cls.FAILED_STATUSES)[0]
    rail, t = make(rail_cls,
                   RailTransportError("ReadTimeout", maybe_sent=True),
                   HttpResponse(503, b""),
                   ok(rail_cls, status=success.upper(), ref="SP-ABC123-C"),
                   ok(rail_cls, status=failed, ref="SP-ABC123-C"),
                   HttpResponse(404, b"{}"))
    assert rail.get_status("SP-ABC123-C").status == RailStatus.SUCCEEDED   # after 2 retries
    assert len(t.calls) == 3 and all(c["method"] == "GET" for c in t.calls)
    assert t.calls[0]["url"].endswith(rail_cls.PATH_STATUS.format(ref="SP-ABC123-C"))
    r = rail.get_status("SP-ABC123-C")
    assert r.status == RailStatus.FAILED and r.failure_code
    assert rail.get_status("SP-ABC123-C").status == RailStatus.UNKNOWN     # 404 on re-query


# --- webhooks -----------------------------------------------------------------------------
def _sign(rail_cls, body: bytes, ts: str | None):
    msg = (ts.encode() + b"." + body) if ts else body
    return hmac.new(b"whsec", msg, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("rail_cls", RAILS)
def test_webhook_signature(rail_cls):
    rail, _ = make(rail_cls)
    success = sorted(rail_cls.SUCCESS_STATUSES)[0]
    body = ok(rail_cls, status=success, ref="SP-ABC123-C").body
    ts = str(int(time.time())) if rail_cls.WEBHOOK_TIMESTAMP_HEADER else None
    headers = {rail_cls.WEBHOOK_SIGNATURE_HEADER.upper(): _sign(rail_cls, body, ts)}  # case-insensitive
    if ts:
        headers[rail_cls.WEBHOOK_TIMESTAMP_HEADER] = ts

    ev = rail.verify_and_parse_webhook(headers, body)
    assert ev.signature_ok and ev.provider_ref == "SP-ABC123-C" and ev.status == RailStatus.SUCCEEDED

    tampered = body.replace(b"SP-ABC123-C", b"SP-ABC999-C")
    bad = rail.verify_and_parse_webhook(headers, tampered)
    assert not bad.signature_ok and bad.raw == {}                   # attacker payload not kept
    assert not rail.verify_and_parse_webhook({}, body).signature_ok
    assert not rail.verify_and_parse_webhook(headers, b"\xff\xfe").signature_ok


def test_webhook_with_timestamp_rejects_replays():
    rail, _ = make(NsanoRail)
    body = b'{"data":{"reference":"SP-1-C","status":"successful"}}'
    old = str(int(time.time()) - 3600)
    headers = {"x-nsano-signature": _sign(NsanoRail, body, old), "x-nsano-timestamp": old}
    assert not rail.verify_and_parse_webhook(headers, body).signature_ok


# --- reconciliation report ----------------------------------------------------------------
@pytest.mark.parametrize("rail_cls", RAILS)
def test_settlement_report_pages_and_parses(rail_cls):
    success = sorted(rail_cls.SUCCESS_STATUSES)[0]
    failed = sorted(rail_cls.FAILED_STATUSES)[0]

    def rec(ref, amount, status):
        r = {}
        for path, value in ((rail_cls.REF_FIELD, ref), (rail_cls.STATUS_FIELD, status),
                            (rail_cls.SETTLEMENT_AMOUNT_FIELD, amount)):
            cur = r
            parts = path.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value
        return r

    def page(records, nxt):
        return ok(rail_cls, **{rail_cls.SETTLEMENT_RECORDS_FIELD: records,
                               rail_cls.SETTLEMENT_NEXT_PAGE_FIELD: nxt})

    rail, t = make(rail_cls,
                   page([rec("A-C", "12.50", success), rec("B-C", "3.00", failed),
                         rec("C-C", "1.005", success)], 2),
                   page([rec("A-C", "12.50", success), rec("D-C", "7.25", success)], None))
    out = rail.settled_transactions(dt.date(2026, 10, 6))
    assert out == {"A-C": 25_00, "D-C": 7_25}        # failed skipped, bad amount skipped, dup summed
    assert "date=2026-10-06" in t.calls[0]["url"] and "page=2" in t.calls[1]["url"]

    rail, _ = make(rail_cls, HttpResponse(500, b""), HttpResponse(500, b""), HttpResponse(500, b""))
    with pytest.raises(RailError):
        rail.settled_transactions(dt.date(2026, 10, 6))


# --- name enquiry ---------------------------------------------------------------------------
@pytest.mark.parametrize("rail_cls", RAILS)
def test_wallet_name_lookup(rail_cls):
    rail, t = make(rail_cls, ok(rail_cls, **{rail_cls.NAME_FIELD: "AMA MENSAH"}),
                   HttpResponse(404, b"{}"), RailTransportError("x", maybe_sent=False),
                   RailTransportError("x", maybe_sent=False), RailTransportError("x", maybe_sent=False))
    found = rail.lookup_wallet_name("mtn", "+233244058519")
    assert found.found and found.account_name == "AMA MENSAH"
    assert not rail.lookup_wallet_name("mtn", "+233244058519").found
    unavailable = rail.lookup_wallet_name("mtn", "+233244058519")
    assert not unavailable.found and "Try again" in unavailable.message


@pytest.mark.parametrize("rail_cls", RAILS)
def test_bank_payout_off_until_partner_gives_endpoint(rail_cls, monkeypatch):
    from apps.rails.exceptions import RailUnsupported
    from apps.rails.types import BankPayoutRequest
    req = BankPayoutRequest(Money(5000, "GHS"), "GCB", "1234567890", "Ama Stores", "SETTLE-9", "x", "k")
    rail, t = make(rail_cls, ok(rail_cls))
    assert rail.supports_bank_payout is False
    with pytest.raises(RailUnsupported):
        rail.bank_payout(req)
    assert t.calls == []

    monkeypatch.setattr(rail_cls, "PATH_BANK_PAYOUT", "/bank-transfer")
    assert rail.supports_bank_payout is True
    result = rail.bank_payout(req)
    assert result.status == RailStatus.PENDING and result.provider_ref == "SETTLE-9-P"
    body = json.dumps(t.calls[0]["body"])
    assert t.calls[0]["url"].endswith("/bank-transfer") and "1234567890" in body and "GCB" in body


def test_dig_helper():
    assert dig({"a": {"b": 1}}, "a.b") == 1 and dig({"a": 1}, "a.b", "x") == "x"
