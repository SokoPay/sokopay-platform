"""Inbound remittance termination (DEMI): signed webhook → wallet credit."""

import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.connectors.remittance import MockRemittanceConnector
from apps.ledger.models import Posting
from apps.licensing.gate import _enabled_set
from apps.wallet import services as wallet
from apps.wallet.models import InboundRemittance

User = get_user_model()
pytestmark = pytest.mark.django_db
URL = "/api/v1/remittance/mock/webhook"


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.REMITTANCE_WEBHOOK_SECRET = "remit-secret"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


def _post(payload: dict, *, sign=True):
    body = json.dumps(payload).encode()
    headers = {"HTTP_X_MOCK_SIGNATURE": MockRemittanceConnector.sign(body) if sign else "bad"}
    return APIClient().post(URL, data=body, content_type="application/json", **headers)


def _payload(ref="R1", amount=250_00, phone="+233244058519", currency="GHS"):
    return {"partner_ref": ref, "recipient_phone": phone, "amount_minor": amount,
            "currency": currency, "sender_name": "Kwame (London)", "sender_country": "GB"}


def test_remittance_credits_wallet_and_balances():
    r = _post(_payload())
    assert r.status_code == 200 and r.json()["status"] == "credited"
    user = User.objects.get(phone="+233244058519")      # created if new
    assert wallet.balance(user) == 250_00
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_duplicate_webhook_credits_once():
    _post(_payload())
    _post(_payload())
    user = User.objects.get(phone="+233244058519")
    assert wallet.balance(user) == 250_00
    assert InboundRemittance.objects.count() == 1


def test_bad_signature_rejected():
    r = _post(_payload(), sign=False)
    assert r.status_code == 401
    assert InboundRemittance.objects.count() == 0


def test_over_limit_is_rejected_for_return_to_sender():
    r = _post(_payload(ref="BIG", amount=3_500_00))     # Minimum tier: GH₵3,000 per transaction
    assert r.json()["status"] == "rejected"
    assert "limit" in r.json()["reason"].lower()
    assert Posting.objects.count() == 0


def test_non_cedi_rejected():
    r = _post(_payload(ref="USD", currency="USD"))
    assert r.json()["status"] == "rejected"


def test_unknown_partner_and_placeholder_partner():
    assert APIClient().post("/api/v1/remittance/nobody/webhook", {}, format="json").status_code == 404
    r = APIClient().post("/api/v1/remittance/remittance_partner/webhook", {}, format="json")
    assert r.status_code == 503


def test_blocked_without_demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_ENHANCED"
    _enabled_set.cache_clear()
    assert _post(_payload()).status_code == 403
