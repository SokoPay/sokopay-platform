"""Merchant portal refunds / disputes / webhooks pages, and the staff dispute queue."""

import socket
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.ledger import accounts
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set
from apps.merchants import disputes, onboarding, qr
from apps.merchants.models import Dispute, MerchantMember, Refund, WebhookEndpoint
from apps.wallet import merchant_pay

from .helpers import PASSWORD, login_verified

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
def shop(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    m = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd", business_type="registered",
                                   trading_name="Ama Stores")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return m


@pytest.fixture
def paid(shop):
    user = User.objects.create_user(phone="+233244058519", full_name="Kofi")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 500_00),
                        credit(accounts.customer_wallet(str(user.id)), 500_00)])
    return user, merchant_pay.pay(user=user, code=qr.static_payload(shop), amount_minor=100_00)


def test_owner_refunds_from_payment_page_cashier_cannot(shop, paid):
    customer, p = paid
    c = login_verified(shop.owner.phone)
    page = c.get(f"/dashboard/payments/{p.reference}/")
    assert page.status_code == 200 and b"Refund" in page.content
    c.post(f"/dashboard/payments/{p.reference}/", {"amount": "25", "reason": "Returned item"})
    r = Refund.objects.get()
    assert r.amount_minor == 25_00 and r.status == "succeeded"
    assert natural_balance_of(accounts.customer_wallet(str(customer.id))) == 425_00

    cashier = User.objects.create_user(phone="+233200000011", password=PASSWORD)
    MerchantMember.objects.create(merchant=shop, user=cashier, role="cashier")
    cc = login_verified(cashier.phone)
    cc.post(f"/dashboard/payments/{p.reference}/", {"amount": "25", "reason": "x"})
    assert Refund.objects.count() == 1


def test_disputes_page_and_staff_decision(shop, paid):
    customer, p = paid
    d = disputes.open_dispute(customer=customer, reference=p.reference, reason="not_received",
                              description="Nothing arrived at all")
    c = login_verified(shop.owner.phone)
    assert b"Nothing arrived" in c.get("/dashboard/disputes/").content
    c.post("/dashboard/disputes/", {"dispute": d.pk, "action": "explain", "response": "Delivered on Monday, signed"})
    d.refresh_from_db()
    assert d.status == "responded"

    ops = User.objects.create_user(phone="+233200000030", password=PASSWORD, user_type="staff")
    ops.groups.add(Group.objects.get(name="operations"))
    support = User.objects.create_user(phone="+233200000031", password=PASSWORD, user_type="staff")
    support.groups.add(Group.objects.get(name="support"))
    assert login_verified(support.phone).get("/dashboard/admin/disputes/").status_code == 403
    o = login_verified(ops.phone)
    assert b"Kofi" in o.get(f"/dashboard/admin/disputes/{d.pk}/").content or \
        customer.phone.encode() in o.get("/dashboard/admin/disputes/").content
    o.post(f"/dashboard/admin/disputes/{d.pk}/", {"decision": "merchant", "note": "Delivery note signed by customer"})
    d.refresh_from_db()
    assert d.status == Dispute.Status.RESOLVED_MERCHANT and d.decided_by == ops


def test_overdue_open_dispute_reaches_staff_queue(shop, paid):
    customer, p = paid
    d = disputes.open_dispute(customer=customer, reference=p.reference, reason="other",
                              description="Something is wrong here")
    Dispute.objects.filter(pk=d.pk).update(respond_by=timezone.now() - timedelta(hours=1))
    ops = User.objects.create_user(phone="+233200000032", password=PASSWORD, user_type="staff")
    ops.groups.add(Group.objects.get(name="operations"))
    assert b"overdue" in login_verified(ops.phone).get("/dashboard/admin/disputes/").content


def test_webhooks_page_add_shows_secret_once(shop, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", p))])
    c = login_verified(shop.owner.phone)
    r = c.post("/dashboard/webhooks/", {"action": "add", "url": "https://shop.example/hook",
                                        "events": ["payment.succeeded"]})
    ep = WebhookEndpoint.objects.get()
    assert ep.secret.encode() in r.content
    assert ep.secret.encode() not in c.get("/dashboard/webhooks/").content
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", p))])
    c.post("/dashboard/webhooks/", {"action": "add", "url": "https://evil.example/hook"})
    assert WebhookEndpoint.objects.count() == 1
