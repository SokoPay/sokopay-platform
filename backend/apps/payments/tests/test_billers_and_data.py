"""
Billers, airtime and data through the connectors layer:
account lookup, refusing placeholder providers, the defensive refund if one slips
through, data bundles end to end, and the related API endpoints.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.ledger.models import Posting
from apps.licensing.gate import _enabled_set
from apps.payments import services
from apps.payments.models import Biller, Payment
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus

User = get_user_model()
pytestmark = pytest.mark.django_db
PHONE = "+233244058519"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    settings.TELCO_ROUTES = {"mtn": "rail", "telecel": "rail", "at": "rail"}
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def user(db):
    return User.objects.create_user(phone=PHONE, full_name="Ama")


@pytest.fixture
def ecg(db):
    return Biller.objects.create(code="ECG_PREPAID", name="ECG Prepaid",
                                 category="electricity", rail_biller_code="ecg_prepaid")


@pytest.fixture
def mtn_data(db):
    return Biller.objects.create(code="MTN_DATA", name="MTN Data", category="data",
                                 rail_biller_code="mtn_data", network="mtn")


@pytest.fixture
def mtn_airtime(db):
    return Biller.objects.create(code="MTN_AIRTIME", name="MTN Airtime", category="airtime",
                                 rail_biller_code="mtn_airtime", network="mtn")


def _complete(payment):
    MockRail.drive(payment.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": payment.rail_ref, "status": "succeeded"}).encode()
    services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    payment.refresh_from_db()
    return payment


# --- account lookup -----------------------------------------------------------
def test_lookup_through_partner(ecg):
    found = services.lookup_account(ecg, "P1234")
    assert found.found and found.account_name == "TEST CUSTOMER 1234"
    missing = services.lookup_account(ecg, "P0000")
    assert not missing.found


def test_lookup_on_placeholder_biller_is_unsupported(ecg):
    ecg.connector = "ecg"   # direct ECG integration — still a placeholder
    ecg.save()
    result = services.lookup_account(ecg, "P1234")
    assert result.supported is False and not result.found


# --- placeholders never take a customer's money ---------------------------------
def test_payment_to_placeholder_biller_is_refused_up_front(user, ecg):
    ecg.connector = "dstv"
    ecg.save()
    with pytest.raises(services.PaymentError):
        services.initiate_bill_payment(
            user=user, biller=ecg, account_ref="P1", amount_minor=50_00,
            network=Network.MTN, payer=PHONE, idempotency_key="ph-1",
        )
    assert Payment.objects.count() == 0


def test_placeholder_hit_at_delivery_refunds_customer(user, ecg):
    """If routing changes to a placeholder after collection, we refund, never keep the money."""
    p = services.initiate_bill_payment(
        user=user, biller=ecg, account_ref="P1", amount_minor=50_00,
        network=Network.MTN, payer=PHONE, idempotency_key="ph-2",
    )
    ecg.connector = "ecg"
    ecg.save()
    p = _complete(p)
    assert p.status == Payment.Status.FAILED
    assert p.failure_code == "provider_unavailable"
    assert sum(x.amount for x in Posting.objects.all()) == 0


# --- airtime and data -----------------------------------------------------------
def test_airtime_delivered_via_telco_connector(user, mtn_airtime):
    p = services.initiate_airtime(
        user=user, biller=mtn_airtime, phone="+233201112222", amount_minor=10_00,
        network=Network.MTN, payer=PHONE, idempotency_key="air-1",
    )
    assert _complete(p).status == Payment.Status.SUCCEEDED


def test_bundles_are_flagged_as_sample(mtn_data):
    bundles = services.list_bundles("mtn")
    assert bundles and all(b.is_sample for b in bundles)


def test_buy_data_bundle_end_to_end(user, mtn_data):
    bundle = services.list_bundles("mtn")[1]
    p = services.initiate_data_bundle(
        user=user, telco="mtn", phone="+233201112222", bundle_code=bundle.code,
        pay_network=Network.TELECEL, payer=PHONE, idempotency_key="data-1",
    )
    assert p.purpose == Payment.Purpose.DATA
    assert p.amount_minor == bundle.price_minor          # price from catalogue, not client
    assert p.product_code == bundle.code
    assert _complete(p).status == Payment.Status.SUCCEEDED
    assert sum(x.amount for x in Posting.objects.all()) == 0


def test_unknown_bundle_rejected(user, mtn_data):
    with pytest.raises(services.PaymentError):
        services.initiate_data_bundle(
            user=user, telco="mtn", phone="+233201112222", bundle_code="NOPE",
            pay_network=Network.MTN, payer=PHONE,
        )


def test_telco_on_placeholder_route_is_unavailable(settings, mtn_data):
    settings.TELCO_ROUTES = {"mtn": "mtn"}   # direct MTN — placeholder
    with pytest.raises(services.PaymentError):
        services.list_bundles("mtn")


# --- API ----------------------------------------------------------------------
def test_api_lookup_bundles_and_data_purchase(user, ecg, mtn_data):
    client = APIClient()
    client.force_authenticate(user=user)

    r = client.get("/api/v1/billers/ECG_PREPAID/lookup", {"account": "P5678"})
    assert r.status_code == 200 and r.json()["account_name"] == "TEST CUSTOMER 5678"

    r = client.get("/api/v1/telcos/mtn/bundles")
    assert r.status_code == 200
    code = r.json()[0]["code"]
    assert r.json()[0]["is_sample"] is True

    r = client.post("/api/v1/payments/data", {
        "telco": "mtn", "phone": "+233201112222", "bundle_code": code,
        "network": "mtn", "payer": PHONE,
    }, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["product_code"] == code
