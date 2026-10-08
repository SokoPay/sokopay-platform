"""Tickets and food from the wallet: server-side price, reserve, confirm or refund."""

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Sum
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set
from apps.wallet import lifestyle

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.ALLOW_MOCK_INTEGRATIONS = True
    settings.LIFESTYLE_PARTNERS = {"ticketing": "mock_ticketing", "food": "mock_food"}
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def kofi(db):
    u = User.objects.create_user(phone="+233244058519", full_name="Kofi")
    u.set_pin(PIN)
    u.save()
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 500_00),
                        credit(accounts.customer_wallet(str(u.id)), 500_00)])
    return u


def _wallet(u):
    return natural_balance_of(accounts.customer_wallet(str(u.id)))


def test_ticket_order_uses_server_price_and_returns_a_code(kofi):
    o = lifestyle.order(user=kofi, category="ticketing", offering_code="AFRO-REG", quantity=2, pin=PIN)
    assert o.status == "succeeded" and o.total_minor == 300_00 and o.token
    assert _wallet(kofi) == 200_00
    assert natural_balance_of(accounts.lifestyle_partner_payable("mock_ticketing")) == 300_00
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


def test_refused_order_is_refunded_and_guards(kofi):
    o = lifestyle.order(user=kofi, category="ticketing", offering_code="GONE-SOLDOUT", quantity=1, pin=PIN)
    assert o.status == "failed" and _wallet(kofi) == 500_00
    with pytest.raises(lifestyle.LifestyleError, match="Insufficient"):
        lifestyle.order(user=kofi, category="ticketing", offering_code="AFRO-VIP", quantity=2, pin=PIN)
    with pytest.raises(lifestyle.LifestyleError, match="isn't available"):
        lifestyle.order(user=kofi, category="food", offering_code="NOPE", quantity=1, pin=PIN)
    with pytest.raises(lifestyle.LifestyleError):
        lifestyle.order(user=kofi, category="food", offering_code="WAAKYE", quantity=1, pin="000000")
    assert _wallet(kofi) == 500_00


def test_not_offered_without_a_partner_and_api(kofi, settings):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(kofi)['access']}")
    assert c.get("/api/v1/lifestyle/food").json()["available"] is True
    r = c.post("/api/v1/lifestyle/orders", {"category": "food", "offering_code": "WAAKYE", "quantity": 1, "pin": PIN},
               format="json")
    assert r.status_code == 201 and r.json()["status"] == "succeeded"
    assert len(c.get("/api/v1/lifestyle/orders").json()["results"]) == 1
    settings.LIFESTYLE_PARTNERS = {"ticketing": "", "food": ""}
    assert c.get("/api/v1/lifestyle/food").json()["available"] is False
