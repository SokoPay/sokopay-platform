"""Merchant API: key authentication + the charge endpoint."""

import pytest
from rest_framework.test import APIClient

from apps.merchants import api_keys
from apps.merchants.models import ApiKey
from apps.payments.models import Payment

pytestmark = pytest.mark.django_db


def _client(raw_key=None):
    client = APIClient()
    if raw_key:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_key}")
    return client


def test_charge_requires_a_valid_key(approved_merchant):
    resp = _client().post("/api/v1/merchant/charges", {}, format="json")
    assert resp.status_code in (401, 403)

    resp = _client("sk_live_deadbeef_nonsense").post(
        "/api/v1/merchant/charges", {}, format="json"
    )
    assert resp.status_code == 401


def test_merchant_can_create_a_charge(approved_merchant):
    _, raw = api_keys.issue_key(approved_merchant, ApiKey.Mode.LIVE)
    resp = _client(raw).post("/api/v1/merchant/charges", {
        "amount": "250.00",
        "network": "mtn",
        "payer": "+233244058519",
        "narrative": "Order 42",
    }, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["status"] == "pending"
    assert body["amount"] == "250.00"
    payment = Payment.objects.get(reference=body["reference"])
    assert payment.purpose == Payment.Purpose.MERCHANT
    assert payment.merchant_id == approved_merchant.id
