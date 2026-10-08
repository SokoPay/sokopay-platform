"""
Merchant push notifications: the deep-link payload contract the Business app relies
on, who receives what, and that merchant alerts only go to the merchant app.
"""

import json
import re

import pytest
from django.contrib.auth import get_user_model

from apps.merchants import checkout
from apps.merchants.models import MerchantMember
from apps.notifications import services as notes
from apps.notifications.models import Notification
from apps.notifications.push.console import ConsolePushProvider
from apps.notifications.push.registry import reset_push_cache
from apps.payments import services as payment_services
from apps.rails.mock import MockRail
from apps.rails.types import Network, RailStatus

User = get_user_model()
pytestmark = pytest.mark.django_db
REF = re.compile(r"^SP-[0-9A-HJKMNP-TV-Z]{10}$")      # must match the app's validator


@pytest.fixture(autouse=True)
def _push(settings):
    settings.PUSH_PROVIDER = "console"
    reset_push_cache()
    ConsolePushProvider.reset()
    yield
    reset_push_cache()


def _member(merchant, phone, role):
    u = User.objects.create_user(phone=phone, full_name=role.title())
    MerchantMember.objects.create(merchant=merchant, user=u, role=role)
    return u


def _momo_payment_succeeds(merchant):
    p = checkout.initiate_merchant_charge(merchant=merchant, amount_minor=40_00, network=Network.MTN,
                                          payer="+233244058519", idempotency_key="n1")
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    p.refresh_from_db()
    return p


def test_momo_payment_notifies_whole_team_with_deep_link(approved_merchant, django_capture_on_commit_callbacks):
    cashier = _member(approved_merchant, "+233200000071", "cashier")
    finance = _member(approved_merchant, "+233200000072", "finance")
    for u in (approved_merchant.owner, cashier, finance):
        notes.register_device(u, platform="android", app="merchant", token=f"tok-{u.phone}-" + "x" * 30)

    with django_capture_on_commit_callbacks(execute=True):
        p = _momo_payment_succeeds(approved_merchant)

    inbox = Notification.objects.filter(title="Payment received")
    assert {n.user for n in inbox} == {approved_merchant.owner, cashier, finance}
    assert len(ConsolePushProvider.sent) == 3
    data = ConsolePushProvider.sent[0]["data"]
    assert data["type"] == "merchant_payment" and data["reference"] == p.reference
    assert REF.match(data["reference"])
    assert all(isinstance(v, str) for v in data.values())          # FCM data must be strings
    assert "+233244058519" not in str(ConsolePushProvider.sent)     # no payer number in pushes


def test_merchant_alerts_skip_the_customer_app(approved_merchant, django_capture_on_commit_callbacks):
    owner = approved_merchant.owner
    notes.register_device(owner, platform="android", app="customer", token="tok-customer-" + "a" * 30)
    notes.register_device(owner, platform="android", app="merchant", token="tok-merchant-" + "b" * 30)
    with django_capture_on_commit_callbacks(execute=True):
        _momo_payment_succeeds(approved_merchant)
    tokens = [s["token"] for s in ConsolePushProvider.sent]
    assert tokens == ["tok-merchant-" + "b" * 30]
    # Untargeted notifications still reach every app.
    ConsolePushProvider.reset()
    with django_capture_on_commit_callbacks(execute=True):
        notes.notify(owner, title="Hi", body="all apps")
    assert len(ConsolePushProvider.sent) == 2


def test_settlement_events_go_to_money_roles_with_settlement_link(approved_merchant):
    from apps.merchants import notifications
    from apps.merchants.models import Settlement, SettlementAccount
    cashier = _member(approved_merchant, "+233200000073", "cashier")
    admin = _member(approved_merchant, "+233200000074", "admin")
    acct = SettlementAccount.objects.create(merchant=approved_merchant, kind="momo", provider="mtn",
                                            account_no="+233244058519", account_name="x")
    s = Settlement.objects.create(merchant=approved_merchant, destination=acct, amount_minor=10_00,
                                  status="paid", requested_by=approved_merchant.owner)
    notifications.settlement_outcome(s)
    got = Notification.objects.filter(title="Settlement paid")
    assert {n.user for n in got} == {approved_merchant.owner, admin}
    assert cashier not in {n.user for n in got}
    assert got.first().data == {"type": "settlement", "settlement": str(s.id)}
    assert got.first().target_app == "merchant"
