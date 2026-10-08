"""Merchant mobile app API: roles, QR requests, settlements with PIN step-up."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.merchants.models import MerchantMember, PaymentRequest, Settlement, SettlementAccount
from apps.notifications.models import Notification
from apps.rails.mock import MockRail

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _member(merchant, phone, role):
    u = User.objects.create_user(phone=phone, full_name=role.title())
    u.set_pin(PIN)
    u.save()
    MerchantMember.objects.create(merchant=merchant, user=u, role=role)
    return u


@pytest.fixture
def shop(approved_merchant):
    approved_merchant.owner.set_pin(PIN)
    approved_merchant.owner.save()
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 500_00),
                        credit(accounts.merchant_payable(str(approved_merchant.id)), 500_00)])
    return approved_merchant


def _verified_account(m, number="+233244058519"):
    a = SettlementAccount.objects.create(merchant=m, kind="momo", provider="mtn", account_no=number,
                                         account_name="Ama Stores", name_check_status="matched",
                                         is_default=True)
    SettlementAccount.objects.filter(pk=a.pk).update(updated_at=timezone.now() - timedelta(days=5))
    return a


def test_requires_auth_and_membership(shop):
    assert APIClient().get("/api/v1/merchant-app/me").status_code in (401, 403)
    outsider = User.objects.create_user(phone="+233200000099")
    assert _client(outsider).get("/api/v1/merchant-app/me").status_code == 403


def test_me_hides_money_from_cashiers(shop):
    owner = _client(shop.owner).get("/api/v1/merchant-app/me").json()
    assert owner["available_minor"] == 500_00 and owner["can_move_money"]
    cashier = _member(shop, "+233200000020", "cashier")
    body = _client(cashier).get("/api/v1/merchant-app/me").json()
    assert "available_minor" not in body and not body["can_view_money"]
    assert _client(cashier).get("/api/v1/merchant-app/settlements").status_code == 403


def test_static_qr_and_payment_request_lifecycle(shop):
    cashier = _client(_member(shop, "+233200000021", "cashier"))
    qr = cashier.get("/api/v1/merchant-app/qr").json()
    assert qr["short_code"] == shop.short_code and qr["payload"].endswith(shop.short_code)

    r = cashier.post("/api/v1/merchant-app/payment-requests",
                     {"amount": "25.00", "description": "Order 7"}, format="json")
    assert r.status_code == 201, r.content
    token = r.json()["token"]
    assert r.json()["amount_minor"] == 25_00 and r.json()["status"] == "open"
    assert cashier.get(f"/api/v1/merchant-app/payment-requests/{token}").json()["status"] == "open"
    assert cashier.post("/api/v1/merchant-app/payment-requests", {"amount": "-3"}, format="json").status_code == 400
    assert cashier.post("/api/v1/merchant-app/payment-requests", {"amount": "abc"}, format="json").status_code == 400

    open_amount = cashier.post("/api/v1/merchant-app/payment-requests", {}, format="json").json()
    assert open_amount["amount_minor"] is None

    cashier.post(f"/api/v1/merchant-app/payment-requests/{token}/cancel")
    assert PaymentRequest.objects.get(token=token).status == "cancelled"
    assert len(cashier.get("/api/v1/merchant-app/payment-requests").json()) == 2


def test_other_merchants_requests_are_invisible(shop, owner):
    from apps.merchants import onboarding
    other_owner = User.objects.create_user(phone="+233200000040")
    other = onboarding.create_merchant(owner=other_owner, legal_name="Other", business_type="registered")
    req = PaymentRequest.objects.create(merchant=other, token="tok-other-123", expires_at=timezone.now())
    assert _client(shop.owner).get(f"/api/v1/merchant-app/payment-requests/{req.token}").status_code == 404


def test_settlement_needs_pin_and_money_role(shop):
    _verified_account(shop)
    owner = _client(shop.owner)
    assert owner.post("/api/v1/merchant-app/settlements", {"amount": "100.00", "pin": "000001"},
                      format="json").status_code == 403                 # wrong PIN
    r = owner.post("/api/v1/merchant-app/settlements", {"amount": "100.00", "pin": PIN}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["status"] == "processing" and not r.json()["needs_approval"]

    admin = _client(_member(shop, "+233200000022", "admin"))
    assert admin.get("/api/v1/merchant-app/settlements").status_code == 200       # can view
    assert admin.post("/api/v1/merchant-app/settlements", {"pin": PIN}, format="json").status_code == 403

    body = owner.get("/api/v1/merchant-app/settlements").json()
    assert body["available_minor"] == 400_00 and len(body["settlements"]) == 1
    assert body["bank_payouts_available"] is True                              # MockRail

    MockRail.drive(Settlement.objects.get().rail_ref, "succeeded")
    from apps.merchants.tasks import poll_settlement_payouts
    poll_settlement_payouts()
    assert owner.get("/api/v1/merchant-app/settlements").json()["settlements"][0]["status"] == "paid"


def test_wrong_pins_lock_the_account(shop):
    _verified_account(shop)
    owner = _client(shop.owner)
    for _ in range(5):
        owner.post("/api/v1/merchant-app/settlements", {"pin": "000001"}, format="json")
    r = owner.post("/api/v1/merchant-app/settlements", {"pin": PIN}, format="json")
    assert r.status_code == 403 and "locked" in r.json()["error"]
    assert not Settlement.objects.exists()


def test_add_settlement_account(shop):
    fin = _member(shop, "+233200000023", "finance")
    c = _client(fin)
    r = c.post("/api/v1/merchant-app/settlement-accounts",
               {"kind": "momo", "provider": "MTN", "account_no": "024 405 8519",
                "account_name": "Ama Stores", "pin": PIN}, format="json")
    assert r.status_code == 201, r.content
    acct = SettlementAccount.objects.get()
    assert acct.provider == "mtn" and acct.account_no == "+233244058519" and acct.is_default
    assert acct.name_check_status == "pending" and not r.json()["verified"]
    assert Notification.objects.filter(user=shop.owner, title="New settlement account added").exists()

    bad = c.post("/api/v1/merchant-app/settlement-accounts",
                 {"kind": "momo", "provider": "glo", "account_no": "0244058519",
                  "account_name": "x", "pin": PIN}, format="json")
    assert bad.status_code == 400
    cashier = _client(_member(shop, "+233200000024", "cashier"))
    assert cashier.post("/api/v1/merchant-app/settlement-accounts",
                        {"kind": "momo", "provider": "mtn", "account_no": "0244058519",
                         "account_name": "x", "pin": PIN}, format="json").status_code == 403


def test_bank_account_refused_when_rail_cannot_pay_banks(shop, monkeypatch):
    monkeypatch.setattr(MockRail, "supports_bank_payout", False)
    r = _client(shop.owner).post("/api/v1/merchant-app/settlement-accounts",
                                 {"kind": "bank", "provider": "gcb", "account_no": "1234567890",
                                  "account_name": "Ama Stores", "pin": PIN}, format="json")
    assert r.status_code == 400 and "Bank settlements" in r.json()["error"]
    assert _client(shop.owner).get("/api/v1/merchant-app/settlements").json()["bank_payouts_available"] is False
