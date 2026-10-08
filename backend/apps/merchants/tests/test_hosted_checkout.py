"""Hosted checkout pages, payment links and API checkout sessions."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from apps.licensing.gate import _enabled_set
from apps.merchants import api_keys, hosted, onboarding, qr
from apps.merchants.checkout import confirm_merchant_collection
from apps.merchants.models import CheckoutSession, PaymentLink, PaymentRequest
from apps.payments.models import Payment
from apps.portal.tests.helpers import PASSWORD, login_verified
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _medium(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"
    settings.RAIL_PROVIDER = "mock"
    settings.QR_BASE_URL = "https://pay.test"
    reset_rail_cache()
    MockRail.reset()
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()
    reset_rail_cache()


@pytest.fixture
def shop(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    m = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd", business_type="registered",
                                   trading_name="Ama Stores")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    qr.ensure_short_code(m)
    return m


def _pay(c, path, **data):
    return c.post(path, {"network": "mtn", "phone": "0244058519", **data})


def test_payment_link_fixed_amount_flow(shop):
    link = hosted.create_link(merchant=shop, title="Kente scarf", amount_minor=120_00, created_by=shop.owner)
    c = Client()
    page = c.get(f"/l/{link.token}")
    assert page.status_code == 200 and b"Kente scarf" in page.content and b"120.00" in page.content
    r = _pay(c, f"/l/{link.token}", amount="1.00")        # client amount ignored for fixed links
    assert r.status_code == 302 and "/checkout/wait/" in r["Location"]
    p = Payment.objects.get()
    assert p.amount_minor == 120_00 and p.source_ref == f"link:{link.token}" and p.payer == "+233244058519"
    assert c.get(f"/checkout/status/{p.reference}").json() == {"status": "pending"}
    confirm_merchant_collection(p)
    link.refresh_from_db()
    assert link.uses == 1 and c.get(f"/checkout/status/{p.reference}").json()["status"] == "succeeded"


def test_open_amount_shop_page_and_closed_links(shop):
    c = Client()
    assert c.get(f"/m/{shop.short_code}").status_code == 200
    r = _pay(c, f"/m/{shop.short_code}", amount="35.50")
    assert r.status_code == 302 and Payment.objects.get().amount_minor == 35_50
    link = hosted.create_link(merchant=shop, title="Once", amount_minor=10_00, max_uses=1, created_by=shop.owner)
    PaymentLink.objects.filter(pk=link.pk).update(uses=1)
    assert c.get(f"/l/{link.token}").status_code == 410
    assert c.get("/l/not-a-token").status_code == 404
    bad = _pay(c, f"/m/{shop.short_code}", amount="10", phone="12345")
    assert b"Ghana mobile money number" in bad.content


def test_prompt_spam_is_limited_per_phone(shop):
    c = Client()
    for _ in range(6):
        assert _pay(c, f"/m/{shop.short_code}", amount="1").status_code == 302
    blocked = _pay(c, f"/m/{shop.short_code}", amount="1")
    assert blocked.status_code == 200 and b"Too many" in blocked.content
    assert Payment.objects.count() == 6


def test_dynamic_qr_paid_on_the_web_closes_the_request(shop):
    req = qr.create_payment_request(merchant=shop, created_by=shop.owner, amount_minor=60_00, description="Order 7")
    c = Client()
    _pay(c, f"/q/{req.token}")
    p = Payment.objects.get()
    confirm_merchant_collection(p)
    req.refresh_from_db()
    assert req.status == PaymentRequest.Status.PAID and req.payment == p
    assert c.get(f"/q/{req.token}").status_code == 410


def test_api_checkout_session_and_redirect(shop):
    key, secret = api_keys.issue_key(shop, "live")
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    bad = api.post("/api/v1/merchant/checkout-sessions", {"amount": "50.00", "success_url": "http://shop.example/ok"},
                   format="json")
    assert bad.status_code == 400
    r = api.post("/api/v1/merchant/checkout-sessions", {"amount": "50.00", "reference": "ORD-9",
                                                         "success_url": "https://shop.example/ok"}, format="json")
    assert r.status_code == 201 and r.json()["url"].startswith("https://pay.test/c/")
    token = r.json()["token"]
    c = Client()
    _pay(c, f"/c/{token}")
    p = Payment.objects.get()
    confirm_merchant_collection(p)
    s = CheckoutSession.objects.get(token=token)
    assert s.status == "paid" and s.payment == p
    wait = c.get(f"/checkout/wait/{p.reference}")
    assert b"https://shop.example/ok?reference=" in wait.content
    # A test-mode key's session can't trigger a real MoMo prompt.
    _, test_secret = api_keys.issue_key(shop, "test")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {test_secret}")
    t = api.post("/api/v1/merchant/checkout-sessions", {"amount": "5.00"}, format="json").json()["token"]
    assert b"test checkout" in _pay(c, f"/c/{t}").content


def test_portal_payment_links_page(shop):
    c = login_verified(shop.owner.phone)
    c.post("/dashboard/payment-links/", {"title": "Delivery fee", "amount": "15"})
    link = PaymentLink.objects.get()
    page = c.get("/dashboard/payment-links/")
    assert page.status_code == 200 and link.token.encode() in page.content
