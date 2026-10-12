"""
Fees & charges: every price type (free, flat, percentage capped, flat + percentage), the
new charges on wallet sends and transfers out, time-limited discounts with maker-checker,
the fee quote the apps show before the PIN, and the admin page.
"""

import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.common.models import AuditEvent
from apps.connectors.transfers import MockTransferConnector
from apps.connectors.types import DestinationType, TransferDestination
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import natural_balance_of
from apps.licensing.gate import _enabled_set
from apps.payments import services as payment_services
from apps.portal.tests.helpers import PASSWORD, login_verified
from apps.pricing import services as pricing
from apps.pricing.models import Promotion
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import interop
from apps.wallet import services as wallet
from apps.wallet.models import ExternalTransfer

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "482915"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.RAIL_PROVIDER = "mock"
    settings.INTEROP_ROUTES = {"momo": "rail", "bank": "mock", "wallet": "mock"}
    reset_rail_cache()
    MockRail.reset()
    MockTransferConnector.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()
    _enabled_set.cache_clear()


@pytest.fixture
def fin(db):
    out = []
    for i in (1, 2):
        u = User.objects.create_user(phone=f"+23320000006{i}", full_name=f"Fin {i}", password=PASSWORD,
                                     user_type="staff")
        u.groups.add(Group.objects.get(name="finance"))
        out.append(u)
    return out


def _person(phone, amount=0):
    u = User.objects.create_user(phone=phone, full_name=f"P {phone[-3:]}")
    u.set_pin(PIN)
    u.save()
    if amount:
        p = wallet.initiate_funding(user=u, amount_minor=amount, network=Network.MTN, payer=u.phone,
                                    idempotency_key=f"fund-{phone}")
        MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
        body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
        payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    return u


def _set_price(fin, product, **kw):
    r = pricing.propose(kind="fee", product=product, note="test price", proposed_by=fin[0], **kw)
    return pricing.decide(r, approver=fin[1], approve=True)


def _balanced():
    return sum(p.amount for p in Posting.objects.all()) == 0


# --- price types ------------------------------------------------------------------------------
def test_new_charges_start_free():
    for product in ("p2p", "transfer_momo", "transfer_bank"):
        assert pricing.fee(product, 1_000_00) == 0


@pytest.mark.parametrize("kw,on_100,on_5000", [
    ({}, 0, 0),                                                     # free
    ({"flat_minor": 50}, 50, 50),                                   # flat GH₵0.50
    ({"percent_bp": 100, "max_minor": 10_00}, 1_00, 10_00),         # 1% capped at GH₵10
    ({"flat_minor": 50, "percent_bp": 50, "min_minor": 1_00}, 1_00, 25_50),   # flat + %, with a minimum
])
def test_price_types(fin, kw, on_100, on_5000):
    _set_price(fin, "p2p", **kw)
    assert pricing.fee("p2p", 100_00) == on_100
    assert pricing.fee("p2p", 5_000_00) == on_5000


# --- fees charged on sends and transfers ---------------------------------------------------------
def test_wallet_send_charges_the_sender_on_top(fin):
    _set_price(fin, "p2p", percent_bp=100, max_minor=5_00)           # 1% capped at GH₵5
    kofi, ama = _person("+233244000201", 500_00), _person("+233244000202")
    result = wallet.send_p2p(sender=kofi, recipient_phone=ama.phone, amount_minor=100_00)
    assert result["fee_minor"] == 1_00
    assert wallet.balance(kofi) == 399_00 and wallet.balance(ama) == 100_00
    assert natural_balance_of(accounts.fee_revenue()) == 1_00 and _balanced()


def test_fee_counts_when_checking_the_balance(fin):
    _set_price(fin, "p2p", flat_minor=1_00)
    kofi, ama = _person("+233244000201", 100_00), _person("+233244000202")
    with pytest.raises(Exception, match="plus the GH₵ 1.00 fee"):
        wallet.send_p2p(sender=kofi, recipient_phone=ama.phone, amount_minor=100_00)
    assert wallet.balance(kofi) == 100_00


def test_transfer_fee_is_earned_on_success_and_refunded_on_failure(fin):
    _set_price(fin, "transfer_bank", flat_minor=2_00)
    _set_price(fin, "transfer_momo", percent_bp=100)
    ama = _person("+233244000202", 500_00)
    ok = interop.send_external(sender=ama, destination=TransferDestination(DestinationType.BANK, "GCB", "1234567890123"),
                               amount_minor=50_00)
    assert ok.status == "succeeded" and ok.fee_minor == 2_00 and wallet.balance(ama) == 448_00
    assert natural_balance_of(accounts.fee_revenue()) == 2_00
    MockTransferConnector.script(RailStatus.FAILED)
    bad = interop.send_external(sender=ama, destination=TransferDestination(DestinationType.WALLET, "zeepay",
                                                                            "+233209998888"), amount_minor=80_00)
    assert bad.status == ExternalTransfer.Status.FAILED and bad.fee_minor == 80
    assert wallet.balance(ama) == 448_00                              # amount AND fee back
    assert natural_balance_of(accounts.fee_revenue()) == 2_00 and _balanced()


# --- discounts ------------------------------------------------------------------------------------
def _promo(fin, approve=True, **kw):
    now = timezone.now()
    args = {"name": "Test discount", "product": "p2p", "percent_off_bp": 5_000, "starts_at": now,
            "ends_at": now + timedelta(days=7), "note": "testing discounts", **kw}
    p = pricing.propose_promotion(proposed_by=fin[0], **args)
    return pricing.decide_promotion(p, approver=fin[1], approve=True) if approve else p


def test_a_discount_applies_only_when_approved_and_running(fin):
    _set_price(fin, "p2p", flat_minor=2_00)
    pending = _promo(fin, approve=False)
    assert pricing.fee("p2p", 100_00) == 2_00                         # not approved yet
    pricing.decide_promotion(pending, approver=fin[1], approve=True)
    q = pricing.quote("p2p", 100_00)
    assert q == {"base": 2_00, "discount": 1_00, "fee": 1_00, "promotion": "Test discount"}
    later = timezone.now() + timedelta(days=8)
    assert pricing.fee("p2p", 100_00, at=later) == 2_00                # over


def test_free_discount_for_every_fee_and_the_best_discount_wins(fin):
    _set_price(fin, "transfer_momo", flat_minor=1_00)
    _promo(fin, name="Half off sends", percent_off_bp=5_000, product="transfer_momo")
    _promo(fin, name="Christmas: everything free", percent_off_bp=10_000, product="all")
    q = pricing.quote("transfer_momo", 100_00)
    assert q["fee"] == 0 and q["promotion"] == "Christmas: everything free"


def test_discount_rules(fin):
    now = timezone.now()
    with pytest.raises(pricing.PricingError, match="different person"):
        p = _promo(fin, approve=False)
        pricing.decide_promotion(p, approver=fin[0], approve=True)
    with pytest.raises(pricing.PricingError, match="after the start"):
        _promo(fin, starts_at=now, ends_at=now - timedelta(hours=1))
    with pytest.raises(pricing.PricingError, match="1% and 100%"):
        _promo(fin, percent_off_bp=50)
    with pytest.raises(pricing.PricingError, match="at most a year"):
        _promo(fin, ends_at=now + timedelta(days=400))


def test_ending_a_discount_early(fin):
    _set_price(fin, "p2p", flat_minor=2_00)
    p = _promo(fin)
    with pytest.raises(pricing.PricingError, match="Say why"):
        pricing.end_promotion(p, actor=fin[0], reason="")
    pricing.end_promotion(p, actor=fin[0], reason="Budget used up")
    assert pricing.fee("p2p", 100_00) == 2_00
    assert AuditEvent.objects.filter(action="pricing.promotion_end").exists()


# --- quote API and admin page --------------------------------------------------------------------
def test_fee_quote_api(fin):
    _set_price(fin, "p2p", percent_bp=100, max_minor=5_00)
    _promo(fin, name="Half off", percent_off_bp=5_000)
    kofi = _person("+233244000201")
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Bearer " + tokens.issue_tokens(kofi)["access"])
    r = c.get("/api/v1/fees/quote", {"product": "p2p", "amount": "200.00"})
    assert r.status_code == 200
    assert r.json()["fee"] == "GH₵ 1.00" and r.json()["base_fee"] == "GH₵ 2.00" and r.json()["promotion"] == "Half off"
    assert r.json()["total"] == "GH₵ 201.00"
    assert c.get("/api/v1/fees/quote", {"product": "nope", "amount": "1"}).status_code == 400
    assert APIClient().get("/api/v1/fees/quote", {"product": "p2p", "amount": "1"}).status_code == 401


def test_fees_page_proposes_prices_and_discounts(fin):
    c1, c2 = login_verified(fin[0].phone), login_verified(fin[1].phone)
    page = c1.get("/dashboard/admin/pricing/")
    assert page.status_code == 200 and b"Fees &amp; charges" in page.content and b"Send to a SokoPay user" in page.content
    c1.post("/dashboard/admin/pricing/", {"action": "propose", "key": "fee:p2p", "ptype": "percent", "percent": "1",
                                          "max": "10", "note": "launch price"})
    rule = pricing.PriceRule.objects.get(product="p2p")
    assert rule.percent_bp == 100 and rule.max_minor == 10_00 and rule.status == "pending"
    c2.post("/dashboard/admin/pricing/", {"action": "approve", "rule": rule.pk})
    assert pricing.fee("p2p", 2_000_00) == 10_00
    ends = (timezone.localtime() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    c1.post("/dashboard/admin/pricing/", {"action": "promo_propose", "name": "Weekend free sends", "product": "p2p",
                                          "promo_type": "free", "ends_at": ends, "note": "weekend promo"})
    promo = Promotion.objects.get()
    c2.post("/dashboard/admin/pricing/", {"action": "promo_approve", "promo": promo.pk})
    assert pricing.fee("p2p", 2_000_00) == 0
    page = c1.get("/dashboard/admin/pricing/")
    assert b"Running" in page.content and b"Weekend free sends" in page.content
