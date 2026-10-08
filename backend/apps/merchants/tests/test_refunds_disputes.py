"""Merchant refunds (wallet + MoMo), customer disputes, settlement holds, APIs."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set
from apps.merchants import checkout, disputes, onboarding, qr, refunds, settlement
from apps.merchants.exceptions import DisputeError, RefundError
from apps.merchants.models import Dispute, MerchantMember, Refund
from apps.payments.models import Payment
from apps.rails.mock import MockRail
from apps.rails.types import Network, RailStatus
from apps.wallet import merchant_pay

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"


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
    owner.set_pin(PIN)
    owner.save()
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


def _wallet_payment(shop, customer, amount=100_00):
    return merchant_pay.pay(user=customer, code=qr.static_payload(shop), amount_minor=amount)


def _momo_payment(shop, amount=100_00, payer="+233244058519"):
    p = checkout.initiate_merchant_charge(merchant=shop, amount_minor=amount, network=Network.MTN, payer=payer)
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    return checkout.confirm_merchant_collection(p)


def _wallet(user):
    return natural_balance_of(accounts.customer_wallet(str(user.id)))


def _payable(m):
    return natural_balance_of(accounts.merchant_payable(str(m.id)))


def _books_balance():
    from django.db.models import Sum
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


# --- refunds ---------------------------------------------------------------------------------
def test_wallet_refund_partial_then_full_and_never_more(shop, customer):
    p = _wallet_payment(shop, customer)
    assert _wallet(customer) == 400_00
    net = 100_00 - p.fee_minor
    r = refunds.create_refund(payment=p, amount_minor=30_00, reason="Item out of stock", requested_by=shop.owner)
    assert r.status == Refund.Status.SUCCEEDED and _wallet(customer) == 430_00
    assert _payable(shop) == net - 30_00
    p.refresh_from_db()
    assert p.status == Payment.Status.SUCCEEDED          # partial: still a sale
    with pytest.raises(RefundError, match="at most"):
        refunds.create_refund(payment=p, amount_minor=80_00, reason="x", requested_by=shop.owner)
    # The merchant doesn't hold enough (fee isn't returned), so a full refund of the rest fails cleanly.
    with pytest.raises(RefundError, match="balance is too low"):
        refunds.create_refund(payment=p, amount_minor=None, reason="rest", requested_by=shop.owner)
    post_entry("top up merchant", [debit(accounts.partner_clearing("mock"), 10_00),
                                   credit(accounts.merchant_payable(str(shop.id)), 10_00)])
    refunds.create_refund(payment=p, amount_minor=None, reason="rest", requested_by=shop.owner)
    p.refresh_from_db()
    assert p.status == Payment.Status.REFUNDED and _wallet(customer) == 500_00
    with pytest.raises(RefundError, match="fully refunded"):
        refunds.create_refund(payment=p, amount_minor=None, reason="again", requested_by=shop.owner)
    _books_balance()


def test_refund_rules(shop, customer):
    p = _wallet_payment(shop, customer)
    with pytest.raises(RefundError, match="reason"):
        refunds.create_refund(payment=p, amount_minor=10_00, reason="  ", requested_by=shop.owner)
    Payment.objects.filter(pk=p.pk).update(completed_at=timezone.now() - timedelta(days=181))
    with pytest.raises(RefundError, match="older than"):
        refunds.create_refund(payment=p, amount_minor=10_00, reason="late", requested_by=shop.owner)
    Payment.objects.filter(pk=p.pk).update(completed_at=timezone.now(), mode="test")
    with pytest.raises(RefundError, match="Test"):
        refunds.create_refund(payment=p, amount_minor=10_00, reason="t", requested_by=shop.owner)


def test_momo_refund_goes_to_original_payer_and_reverses_on_failure(shop):
    p = _momo_payment(shop)
    before = _payable(shop)
    r = refunds.create_refund(payment=p, amount_minor=50_00, reason="Wrong size", requested_by=shop.owner)
    assert r.status == Refund.Status.PROCESSING and r.destination == "momo" and r.rail_ref
    assert MockRail._records[r.rail_ref][:2] == ("payout", 50_00)
    assert _payable(shop) == before - 50_00
    MockRail.drive(r.rail_ref, RailStatus.FAILED)
    refunds.resolve_processing()
    r.refresh_from_db()
    assert r.status == Refund.Status.FAILED and _payable(shop) == before
    assert refunds.refundable_minor(p) == 100_00        # failed refunds don't count
    r2 = refunds.create_refund(payment=p, amount_minor=50_00, reason="Retry", requested_by=shop.owner)
    MockRail.drive(r2.rail_ref, RailStatus.SUCCEEDED)
    refunds.handle_rail_event(r2.rail_ref)
    r2.refresh_from_db()
    assert r2.status == Refund.Status.SUCCEEDED
    refunds.apply_outcome(r2, RailStatus.FAILED)         # late contradictory signal: ignored
    r2.refresh_from_db()
    assert r2.status == Refund.Status.SUCCEEDED and _payable(shop) == before - 50_00
    _books_balance()


def test_card_payments_are_not_auto_refunded(shop):
    p = _momo_payment(shop)
    Payment.objects.filter(pk=p.pk).update(network="card")
    with pytest.raises(RefundError, match="Card"):
        refunds.create_refund(payment=p, amount_minor=10_00, reason="x", requested_by=shop.owner)


# --- disputes ---------------------------------------------------------------------------------
def test_dispute_holds_settlement_and_merchant_refund_resolves_it(shop, customer):
    p = _wallet_payment(shop, customer)
    available = settlement.available_balance_minor(shop)
    d = disputes.open_dispute(customer=customer, reference=p.reference, reason="not_received",
                              description="Paid but the shop said the system was down")
    assert d.amount_minor == 100_00
    assert settlement.available_balance_minor(shop) == max(0, available - 100_00)
    with pytest.raises(DisputeError, match="already an open dispute"):
        disputes.open_dispute(customer=customer, reference=p.reference, reason="other", description="again again")
    post_entry("top up merchant", [debit(accounts.partner_clearing("mock"), 10_00),
                                   credit(accounts.merchant_payable(str(shop.id)), 10_00)])
    disputes.merchant_respond(d, by=shop.owner, accept=True, response="Sorry")
    d.refresh_from_db()
    assert d.status == Dispute.Status.RESOLVED_CUSTOMER and _wallet(customer) == 500_00
    assert d.refunds.count() == 1
    with pytest.raises(DisputeError):
        disputes.merchant_respond(d, by=shop.owner, accept=True)     # no double refund


def test_failed_merchant_refund_reopens_the_dispute(shop, customer):
    p = _wallet_payment(shop, customer)
    d = disputes.open_dispute(customer=customer, reference=p.reference, reason="wrong_amount",
                              description="I was charged too much here")
    with pytest.raises(DisputeError, match="too low"):          # merchant kept the fee, can't cover 100%
        disputes.merchant_respond(d, by=shop.owner, accept=True)
    d.refresh_from_db()
    assert d.status == Dispute.Status.OPEN and not d.refunds.exists()


def test_only_the_payer_can_dispute_and_staff_decide_after_deadline(shop, customer):
    p = _wallet_payment(shop, customer, amount=50_00)
    stranger = User.objects.create_user(phone="+233244000999")
    with pytest.raises(DisputeError, match="couldn't find"):
        disputes.open_dispute(customer=stranger, reference=p.reference, reason="other", description="not mine at all")
    d = disputes.open_dispute(customer=customer, reference=p.reference, reason="not_as_described",
                              description="Shoes were broken on arrival", amount_minor=20_00)
    staff = User.objects.create_user(phone="+233200000099", user_type="staff")
    with pytest.raises(DisputeError, match="still has time"):
        disputes.decide(d, staff=staff, for_customer=True, note="Photos show damage clearly")
    Dispute.objects.filter(pk=d.pk).update(respond_by=timezone.now() - timedelta(minutes=1))
    assert d in disputes.for_staff_queue()
    with pytest.raises(DisputeError, match="reason"):
        disputes.decide(d, staff=staff, for_customer=True, note="ok")
    disputes.decide(d, staff=staff, for_customer=True, note="Photos show damage clearly")
    d.refresh_from_db()
    assert d.status == Dispute.Status.RESOLVED_CUSTOMER and d.decided_by == staff
    assert _wallet(customer) == 500_00 - 50_00 + 20_00


def test_momo_payer_can_dispute_by_phone(shop):
    payer = User.objects.create_user(phone="+233244058600")
    p = _momo_payment(shop, payer=payer.phone)
    d = disputes.open_dispute(customer=payer, reference=p.reference, reason="duplicate",
                              description="Charged twice for one order")
    disputes.merchant_respond(d, by=shop.owner, accept=False, response="Only one charge on our side")
    d.refresh_from_db()
    assert d.status == Dispute.Status.RESPONDED
    disputes.decide(d, staff=shop.owner, for_customer=False, note="Rail shows a single debit")
    d.refresh_from_db()
    assert d.status == Dispute.Status.RESOLVED_MERCHANT


# --- APIs ----------------------------------------------------------------------------------------
def _client(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(user)['access']}")
    return c


def test_customer_and_merchant_app_api(shop, customer):
    p = _wallet_payment(shop, customer)
    cc = _client(customer)
    receipt = cc.get(f"/api/v1/payments/{p.reference}").json()
    assert receipt["can_dispute"] is True and receipt["dispute"] is None
    r = cc.post("/api/v1/disputes", {"reference": p.reference, "reason": "not_received",
                                     "description": "Nothing was delivered to me"}, format="json")
    assert r.status_code == 201, r.content
    assert cc.get(f"/api/v1/payments/{p.reference}").json()["can_dispute"] is False

    cashier = User.objects.create_user(phone="+233200000011")
    MerchantMember.objects.create(merchant=shop, user=cashier, role="cashier")
    assert _client(cashier).get("/api/v1/merchant-app/disputes").status_code == 403

    mc = _client(shop.owner)
    listing = mc.get("/api/v1/merchant-app/disputes").json()
    assert len(listing["results"]) == 1 and "•••" in listing["results"][0]["customer"]
    # Refund blocked while a dispute is open; wrong PIN blocked.
    bad = mc.post(f"/api/v1/merchant-app/payments/{p.reference}/refund",
                  {"reason": "x", "pin": "000000"}, format="json")
    assert bad.status_code == 403
    blocked = mc.post(f"/api/v1/merchant-app/payments/{p.reference}/refund",
                      {"reason": "x", "pin": PIN}, format="json")
    assert blocked.status_code == 400 and "dispute" in blocked.json()["error"]
    did = listing["results"][0]["id"]
    resp = mc.post(f"/api/v1/merchant-app/disputes/{did}/respond",
                   {"accept": False, "response": "Customer collected it in store"}, format="json")
    assert resp.status_code == 200 and resp.json()["status"] == "responded"

    p2 = _wallet_payment(shop, customer, amount=20_00)
    ok = mc.post(f"/api/v1/merchant-app/payments/{p2.reference}/refund",
                 {"amount": "5.00", "reason": "Discount", "pin": PIN}, format="json")
    assert ok.status_code == 201, ok.content
    detail = mc.get(f"/api/v1/merchant-app/payments/{p2.reference}").json()
    assert detail["refunds"][0]["amount_minor"] == 5_00 and detail["refundable_minor"] == 15_00
