"""Merchant app payment history: roles, masking, filters, summary, cursor paging."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.merchants import onboarding
from apps.merchants.models import MerchantMember, PaymentRequest
from apps.payments.models import Payment

User = get_user_model()
pytestmark = pytest.mark.django_db
URL = "/api/v1/merchant-app/payments"


def _pay(merchant, n, *, amount=100_00, fee=150, status="succeeded", mode="live",
         network="mtn", source="momo", age=timedelta(0)):
    p = Payment.objects.create(
        reference=f"SP-T{n:05d}", purpose="merchant", status=status, merchant=merchant,
        amount_minor=amount, fee_minor=fee, total_minor=amount, network=network,
        funding_source=source, payer="+233244058519", rail="mock", mode=mode,
        completed_at=timezone.now() if status == "succeeded" else None,
        failure_code="rail_declined" if status == "failed" else "",
    )
    if age:
        Payment.objects.filter(pk=p.pk).update(created_at=timezone.now() - age)
    return p


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _cashier(merchant):
    u = User.objects.create_user(phone="+233200000050", full_name="Cashier")
    MerchantMember.objects.create(merchant=merchant, user=u, role="cashier")
    return u


def test_owner_sees_net_and_summary_cashier_does_not(approved_merchant):
    _pay(approved_merchant, 1, amount=120_00, fee=180)
    _pay(approved_merchant, 2, amount=50_00, fee=75, status="failed")
    body = _client(approved_merchant.owner).get(URL).json()
    assert {r["reference"] for r in body["results"]} == {"SP-T00001", "SP-T00002"}
    paid = next(r for r in body["results"] if r["status"] == "succeeded")
    assert paid["net_minor"] == 120_00 - 180 and paid["fee_display"] == "GH₵ 1.80"
    assert paid["status_display"] == "Paid" and paid["method"] == "MTN MoMo"
    assert paid["payer"] == "+23324•••8519"                       # masked, never in full
    assert "+233244058519" not in str(body)
    failed = next(r for r in body["results"] if r["status"] == "failed")
    assert "Declined" in failed["failure"]
    assert body["summary"] == {"paid_count": 1, "gross_display": "GH₵ 120.00",
                               "fees_display": "GH₵ 1.80", "net_display": "GH₵ 118.20"}

    cashier = _client(_cashier(approved_merchant)).get(URL).json()
    assert cashier["summary"] is None
    assert all("net_minor" not in r and "fee_minor" not in r for r in cashier["results"])


def test_filters_and_periods(approved_merchant):
    _pay(approved_merchant, 1)
    _pay(approved_merchant, 2, status="pending")
    _pay(approved_merchant, 3, status="failed")
    _pay(approved_merchant, 4, age=timedelta(days=10))
    _pay(approved_merchant, 5, mode="test")
    c = _client(approved_merchant.owner)
    refs = lambda q: {r["reference"] for r in c.get(URL, q).json()["results"]}  # noqa: E731
    assert refs({}) == {"SP-T00001", "SP-T00002", "SP-T00003", "SP-T00004"}   # live only
    assert refs({"status": "paid"}) == {"SP-T00001", "SP-T00004"}
    assert refs({"status": "pending"}) == {"SP-T00002"}
    assert refs({"period": "7d"}) == {"SP-T00001", "SP-T00002", "SP-T00003"}
    assert refs({"mode": "test"}) == {"SP-T00005"}
    assert c.get(URL, {"status": "nope"}).status_code == 400
    assert c.get(URL, {"period": "year"}).status_code == 400
    assert c.get(URL, {"status": "paid", "period": "7d"}).json()["summary"]["paid_count"] == 1


def test_cursor_paging_is_complete_and_stable(approved_merchant):
    for i in range(65):
        _pay(approved_merchant, i, age=timedelta(minutes=i))
    c = _client(approved_merchant.owner)
    seen, cursor, pages = [], None, 0
    while True:
        body = c.get(URL, {"cursor": cursor} if cursor else {}).json()
        seen += [r["reference"] for r in body["results"]]
        pages += 1
        # A new payment arriving mid-scroll must not shift or duplicate later pages.
        if pages == 1:
            _pay(approved_merchant, 999)
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 65 == len(set(seen))
    assert seen[0] == "SP-T00000" and seen[-1] == "SP-T00064"           # newest → oldest
    assert c.get(URL, {"cursor": "garbage!"}).status_code == 400


def test_detail_and_merchant_isolation(approved_merchant):
    p = _pay(approved_merchant, 1)
    PaymentRequest.objects.create(merchant=approved_merchant, token="tok-abc-123", amount_minor=100_00,
                                  description="Table 4", status="paid", payment=p,
                                  expires_at=timezone.now())
    c = _client(approved_merchant.owner)
    body = c.get(f"{URL}/{p.reference}").json()
    assert body["note"] == "Table 4" and body["net_display"] == "GH₵ 98.50"
    assert c.get(URL).json()["results"][0]["note"] == "Table 4"

    other_owner = User.objects.create_user(phone="+233200000060")
    other = onboarding.create_merchant(owner=other_owner, legal_name="Other", business_type="registered")
    assert _client(other_owner).get(f"{URL}/{p.reference}").status_code == 404
    assert _client(other_owner).get(URL).json()["results"] == []
    assert other.payments.count() == 0


def test_wallet_payments_are_labelled(approved_merchant):
    _pay(approved_merchant, 1, network="wallet", source="wallet")
    r = _client(approved_merchant.owner).get(URL).json()["results"][0]
    assert r["method"] == "SokoPay wallet"
