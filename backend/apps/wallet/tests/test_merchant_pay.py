"""Scan-to-pay: QR resolution and paying a merchant from the wallet (DEMI)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding, qr
from apps.merchants.exceptions import MerchantError
from apps.merchants.models import PaymentRequest
from apps.payments.models import Payment
from apps.wallet import merchant_pay
from apps.wallet import services as wallet
from apps.wallet.exceptions import WalletError

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
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner")
    m = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd",
                                   business_type="registered", trading_name="Ama Stores")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return m


@pytest.fixture
def customer(db):
    user = User.objects.create_user(phone="+233244058519", full_name="Kofi")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 500_00),
                        credit(accounts.customer_wallet(str(user.id)), 500_00)])
    return user


def test_static_qr_resolves_from_url_and_bare_code(shop):
    assert len(shop.short_code) == 8
    for code in (qr.static_payload(shop), shop.short_code, shop.short_code.lower()):
        r = qr.resolve(code)
        assert r["type"] == "static" and r["merchant_name"] == "Ama Stores"
        assert r["amount_minor"] is None


def test_unknown_and_foreign_codes_are_rejected(shop):
    for bad in ("", "ZZZZZZZZ", "https://evil.example/m/" + shop.short_code + "x",
                "https://pay.test/q/not-a-token", "x" * 300):
        with pytest.raises(MerchantError):
            qr.resolve(bad)


def test_dynamic_request_fixed_amount_cannot_be_underpaid(shop, customer):
    req = qr.create_payment_request(merchant=shop, created_by=shop.owner,
                                    amount_minor=120_00, description="Order 42")
    # The customer's app sends a smaller amount — ignored; the request amount wins.
    payment = merchant_pay.pay(user=customer, code=qr.request_payload(req), amount_minor=5_00)
    assert payment.status == Payment.Status.SUCCEEDED
    assert payment.amount_minor == 120_00 and payment.funding_source == "wallet"
    assert payment.fee_minor == 180                       # 1.5% MDR
    assert wallet.balance(customer) == 380_00
    assert natural_balance_of(accounts.merchant_payable(str(shop.id))) == 120_00 - 180
    assert natural_balance_of(accounts.fee_revenue()) == 180
    req.refresh_from_db()
    assert req.status == PaymentRequest.Status.PAID and req.payment_id == payment.id
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_request_can_only_be_paid_once(shop, customer):
    req = qr.create_payment_request(merchant=shop, created_by=shop.owner, amount_minor=10_00)
    merchant_pay.pay(user=customer, code=req.token)
    with pytest.raises(WalletError):
        merchant_pay.pay(user=customer, code=req.token)
    assert wallet.balance(customer) == 490_00


def test_expired_request_rejected(shop, customer):
    req = qr.create_payment_request(merchant=shop, created_by=shop.owner, amount_minor=10_00)
    PaymentRequest.objects.filter(pk=req.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(WalletError):
        merchant_pay.pay(user=customer, code=req.token)
    req.refresh_from_db()
    assert req.status == PaymentRequest.Status.EXPIRED


def test_static_qr_needs_an_amount_and_checks_balance(shop, customer):
    with pytest.raises(WalletError):
        merchant_pay.pay(user=customer, code=shop.short_code)                 # no amount
    with pytest.raises(WalletError):
        merchant_pay.pay(user=customer, code=shop.short_code, amount_minor=900_00)  # > balance
    merchant_pay.pay(user=customer, code=shop.short_code, amount_minor=50_00)
    assert wallet.balance(customer) == 450_00


def test_owner_cannot_pay_own_shop(shop):
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(shop.owner.id)), 100_00)])
    with pytest.raises(WalletError):
        merchant_pay.pay(user=shop.owner, code=shop.short_code, amount_minor=10_00)


def test_needs_demi(settings, shop, customer):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"      # aggregation yes, wallet no
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        merchant_pay.pay(user=customer, code=shop.short_code, amount_minor=10_00)


def test_api_resolve_and_pay(shop, customer):
    client = APIClient()
    client.force_authenticate(user=customer)
    r = client.get("/api/v1/pay/resolve", {"code": shop.short_code})
    assert r.status_code == 200 and r.json()["merchant_name"] == "Ama Stores"
    assert client.get("/api/v1/pay/resolve", {"code": "NOPE1234"}).status_code == 404

    r = client.post("/api/v1/wallet/pay-merchant",
                    {"code": shop.short_code, "amount": "25.00"}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["merchant"] == "Ama Stores" and r.json()["status"] == "succeeded"
