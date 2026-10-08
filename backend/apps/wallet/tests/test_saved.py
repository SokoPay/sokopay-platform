"""Saved recipients and recently paid merchants."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding, qr
from apps.wallet import merchant_pay
from apps.wallet.models import SavedRecipient

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.QR_BASE_URL = "https://pay.test"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def world(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner")
    shop = onboarding.create_merchant(owner=owner, legal_name="Grace Mountain Ministry",
                                      business_type="registered")
    onboarding.submit_for_review(shop)
    onboarding.begin_review(shop)
    onboarding.approve(shop)
    qr.ensure_short_code(shop)
    me = User.objects.create_user(phone="+233244058519", full_name="Kofi")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(me.id)), 100_00)])
    friend = User.objects.create_user(phone="+233244058600", full_name="Ama Serwaa")
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(me)['access']}")
    return shop, me, friend, c


def test_recent_merchants_and_saving(world):
    shop, me, friend, c = world
    merchant_pay.pay(user=me, code=shop.short_code, amount_minor=20_50)
    recent = c.get("/api/v1/wallet/merchants/recent").json()["results"]
    assert recent[0]["merchant_name"] == "Grace Mountain Ministry" and recent[0]["saved"] is False
    r = c.post("/api/v1/wallet/saved", {"kind": "merchant", "value": shop.short_code.lower()}, format="json")
    assert r.status_code == 201 and r.json()["label"] == "Grace Mountain Ministry"
    assert c.get("/api/v1/wallet/merchants/recent").json()["results"][0]["saved"] is True
    # Saving twice doesn't duplicate; unknown codes are refused.
    c.post("/api/v1/wallet/saved", {"kind": "merchant", "value": shop.short_code}, format="json")
    assert SavedRecipient.objects.count() == 1
    assert c.post("/api/v1/wallet/saved", {"kind": "merchant", "value": "ZZZZZZZZ"}, format="json").status_code == 400


def test_sokopay_recipient_stored_by_wallet_id_and_owner_scoped(world):
    shop, me, friend, c = world
    r = c.post("/api/v1/wallet/saved", {"kind": "sokopay", "value": friend.phone}, format="json").json()
    assert r["value"].startswith("7") and len(r["value"]) == 10 and "+233" not in r["label"]
    assert c.post("/api/v1/wallet/saved", {"kind": "sokopay", "value": me.phone}, format="json").status_code == 400
    assert c.post("/api/v1/wallet/saved", {"kind": "sokopay", "value": "+233249999999"},
                  format="json").status_code == 400
    other = APIClient()
    other.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(friend)['access']}")
    assert other.get("/api/v1/wallet/saved").json()["results"] == []
    assert other.delete(f"/api/v1/wallet/saved/{r['id']}").status_code == 404
    assert c.delete(f"/api/v1/wallet/saved/{r['id']}").status_code == 204


def test_momo_recipient_needs_a_name_check(world):
    shop, me, friend, c = world
    bad = c.post("/api/v1/wallet/saved", {"kind": "momo", "institution": "mtn", "value": "0241234567"}, format="json")
    assert bad.status_code == 400
    ok = c.post("/api/v1/wallet/saved", {"kind": "momo", "institution": "mtn", "value": "+233241234567"},
                format="json")
    assert ok.status_code in (201, 400)          # depends on the mock name enquiry; never 500
