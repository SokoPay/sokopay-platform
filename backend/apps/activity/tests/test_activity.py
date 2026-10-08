"""
Customer activity feed: one history over payments, transfers and wallet movements.

Checks: every kind of event appears exactly once (wallet-funded payments and transfers
are not double-listed from the ledger), counterparties are named politely, nothing
leaks phone numbers, filters and summary are right, and cursor paging across the
three sources is complete — including rows with identical timestamps.
"""

import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.activity import services as activity
from apps.agents import services as agents
from apps.connectors.transfers import MockTransferConnector
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding
from apps.payments import services as payment_services
from apps.payments.models import Biller, Payment
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import interop, merchant_pay
from apps.wallet import services as wallet
from apps.connectors.types import DestinationType, TransferDestination
from apps.wallet.models import ExternalTransfer

User = get_user_model()
pytestmark = pytest.mark.django_db
URL = "/api/v1/activity"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.INTEROP_ROUTES = {"momo": "mock", "bank": "mock", "wallet": "mock"}
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    MockTransferConnector.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()
    _enabled_set.cache_clear()


@pytest.fixture
def ama(db):
    u = User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 1_000_00),
                        credit(accounts.customer_wallet(str(u.id)), 1_000_00)])
    return u


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _complete(p):
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)


def _everything(ama):
    """One of each kind of event for Ama. The seed entry itself isn't a feed item."""
    ecg = Biller.objects.create(code="ECG_PREPAID", name="ECG Prepaid", category="electricity",
                                rail_biller_code="ECG_PREPAID")
    # bill by MoMo
    _complete(payment_services.initiate_bill_payment(user=ama, biller=ecg, account_ref="0123456789",
                                                     amount_minor=50_00, network=Network.MTN,
                                                     payer=ama.phone, idempotency_key="b1"))
    # bill from the wallet (Payment row + wallet posting → must appear once)
    payment_services.initiate_bill_payment(user=ama, biller=ecg, account_ref="0123456789",
                                           amount_minor=20_00, source="wallet", idempotency_key="b2")
    # p2p both ways
    kofi = User.objects.create_user(phone="+233200000002", full_name="Kofi Boateng")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(kofi.id)), 100_00)])
    wallet.send_p2p(sender=ama, recipient_phone=kofi.phone, amount_minor=30_00)
    wallet.send_p2p(sender=kofi, recipient_phone=ama.phone, amount_minor=10_00)
    # agent cash-in
    agent_user = User.objects.create_user(phone="+233200000003", full_name="Yaw")
    a = agents.register_agent(user=agent_user, display_name="Yaw's Kiosk")
    agents.activate_agent(a)
    agents.topup_float(agent=a, amount_minor=500_00)
    agents.cash_in(agent=a, customer_phone=ama.phone, amount_minor=40_00)
    # external transfer (reserve + settle postings → must appear once)
    interop.send_external(sender=ama, destination=TransferDestination(DestinationType.BANK, "GCB", "1234567890"),
                          amount_minor=25_00)
    # shop via QR from the wallet
    owner = User.objects.create_user(phone="+233200000004")
    shop = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd", business_type="registered",
                                      trading_name="Corner Shop")
    onboarding.submit_for_review(shop)
    onboarding.begin_review(shop)
    onboarding.approve(shop)
    merchant_pay.pay(user=ama, code=shop.short_code, amount_minor=15_00)
    return kofi


def test_every_event_once_with_titles_and_no_leaks(ama):
    _everything(ama)
    body = _client(ama).get(URL).json()
    rows = body["results"]
    cats = sorted(r["category"] for r in rows)
    assert cats == sorted(["bill", "bill", "sent", "received", "cash_in", "transfer", "shop"])
    titles = {r["category"]: r["title"] for r in rows}
    assert titles["sent"] == "To Kofi B." and titles["received"] == "From Kofi B."
    assert titles["cash_in"] == "Cash deposit · Yaw's Kiosk"
    assert titles["shop"] == "Corner Shop" and titles["transfer"] == "To TEST RECIPIENT 7890"
    bills = [r for r in rows if r["category"] == "bill"]
    assert {b["subtitle"].split(" · ")[0] for b in bills} == {"MTN MoMo", "SokoPay wallet"}
    received = next(r for r in rows if r["category"] == "received")
    assert received["direction"] == "in" and received["amount_display"] == "+ GH₵ 10.00"
    # No phone numbers (ledger narratives contain them) anywhere in the response.
    text = json.dumps(body)
    assert "+233200000002" not in text and "200000002" not in text
    # Summary counts what the customer was actually charged: bills include the GH₵0.50
    # fee. out = 50.50 + 20.50 + 30 + 25 + 15 ; in = 10 + 40.
    assert body["summary"] == {"money_in_display": "GH₵ 50.00", "money_out_display": "GH₵ 141.00"}


def test_filters(ama):
    _everything(ama)
    c = _client(ama)
    cats = lambda f: sorted(r["category"] for r in c.get(URL, {"filter": f}).json()["results"])  # noqa: E731
    assert cats("payments") == ["bill", "bill", "shop"]
    assert cats("transfers") == ["received", "sent", "transfer"]
    assert cats("wallet") == ["cash_in"]
    assert c.get(URL, {"filter": "nope"}).status_code == 400
    assert c.get(URL, {"period": "year"}).status_code == 400
    assert c.get(URL, {"cursor": "bad"}).status_code == 400


def test_period_filter(ama):
    _everything(ama)
    Payment.objects.filter(user=ama).update(created_at=timezone.now() - timedelta(days=10))
    rows = _client(ama).get(URL, {"period": "7d"}).json()["results"]
    assert all(r["source"] != "payment" for r in rows) and rows


def test_paging_across_sources_with_identical_timestamps(ama):
    _everything(ama)
    # Make every feed row share one timestamp: only the (source, id) tie-break orders them.
    t = timezone.now()
    Payment.objects.filter(user=ama).update(created_at=t)
    ExternalTransfer.objects.filter(sender=ama).update(created_at=t)
    Posting.objects.filter(account=accounts.customer_wallet(str(ama.id))).update(created_at=t)
    full = [r["id"] for r in activity.feed(ama, size=100)["results"]]
    seen, cursor = [], None
    while True:
        page = activity.feed(ama, cursor=cursor, size=2)
        seen += [r["id"] for r in page["results"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == full and len(seen) == len(set(seen)) == 7


def test_only_my_activity(ama):
    kofi = _everything(ama)
    rows = _client(kofi).get(URL).json()["results"]
    assert sorted(r["category"] for r in rows) == ["received", "sent"]      # Kofi's own view
    assert next(r for r in rows if r["category"] == "received")["title"] == "From Ama M."
    stranger = User.objects.create_user(phone="+233200000009")
    assert _client(stranger).get(URL).json()["results"] == []
