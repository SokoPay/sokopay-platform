"""API-level tests for the payments endpoints (HTTP layer + auth + webhook route)."""

import json

import pytest
from rest_framework.test import APIClient

from apps.licensing.gate import _enabled_set
from apps.payments.models import Biller, Payment
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import RailStatus

User = pytest.importorskip("django.contrib.auth").get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.RAIL_WEBHOOK_SECRET = "test-secret"
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(phone="+233200000000", full_name="Kofi")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def ecg(db):
    return Biller.objects.create(
        code="ECG_PREPAID", name="ECG Prepaid", category="electricity",
        rail_biller_code="ecg_prepaid",
    )


def test_bill_endpoint_creates_pending_payment(auth_client, ecg):
    resp = auth_client.post("/api/v1/payments/bill", {
        "biller_code": "ECG_PREPAID",
        "account": "P123456789",
        "amount": "100.00",
        "network": "mtn",
        "payer": "+233244058519",
    }, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["status"] == "pending"
    assert body["amount"] == "100.00"
    assert body["total"] == "100.50"          # includes the GH₵0.50 fee
    assert body["payer"].startswith("+23324")  # masked
    assert "•" in body["payer"]


def test_requires_authentication(ecg):
    resp = APIClient().post("/api/v1/payments/bill", {}, format="json")
    assert resp.status_code in (401, 403)


def test_webhook_completes_payment_and_rejects_forgery(auth_client, ecg):
    # Create a payment via the API.
    created = auth_client.post("/api/v1/payments/bill", {
        "biller_code": "ECG_PREPAID", "account": "P1", "amount": "50.00",
        "network": "mtn", "payer": "+233244058519",
    }, format="json").json()
    payment = Payment.objects.get(reference=created["reference"])

    MockRail.drive(payment.rail_ref, RailStatus.SUCCEEDED)
    payload = json.dumps({"provider_ref": payment.rail_ref, "status": "succeeded"}).encode()
    good_sig = MockRail().sign(payload)

    # A forged signature is rejected.
    forged = APIClient().post(
        "/api/v1/rails/mock/webhook", data=payload, content_type="application/json",
        HTTP_X_MOCK_SIGNATURE="forged",
    )
    assert forged.status_code == 401
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING

    # The genuine callback completes the payment.
    ok = APIClient().post(
        "/api/v1/rails/mock/webhook", data=payload, content_type="application/json",
        HTTP_X_MOCK_SIGNATURE=good_sig,
    )
    assert ok.status_code == 200
    payment.refresh_from_db()
    assert payment.status == Payment.Status.SUCCEEDED
