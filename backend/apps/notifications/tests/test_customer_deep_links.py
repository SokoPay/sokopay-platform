"""
Customer-app notification contract: every customer notification carries a `type` the
app can route on, targets the customer app only, and links to things the customer can
actually open (their own payment receipt).

Contract (mobile/packages/sokopay_shared + customer/lib/core/deep_links.dart):
  {"type": "payment",  "reference": "SP-…"}  → /payments/<ref> (receipt)
  {"type": "wallet"}                         → /wallet
  {"type": "transfer", "reference": "SP-…"}  → /transfer
  {"type": "marketplace"}                    → /marketplace
"""

import json
import re

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.gate import _enabled_set
from apps.notifications.models import Notification
from apps.payments import services as payment_services
from apps.payments.models import Biller
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import services as wallet

User = get_user_model()
pytestmark = pytest.mark.django_db
REF = re.compile(r"^SP-[0-9A-HJKMNP-TV-Z]{10}$")
CUSTOMER_TYPES = {"payment", "wallet", "transfer", "marketplace"}


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()
    _enabled_set.cache_clear()


@pytest.fixture
def ama(db):
    return User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")


def _fund(user, minor):
    post_entry("seed", [debit(accounts.partner_clearing("mock"), minor),
                        credit(accounts.customer_wallet(str(user.id)), minor)])


def _assert_contract(note):
    assert note.target_app == "customer", note.title
    assert note.data.get("type") in CUSTOMER_TYPES, (note.title, note.data)
    assert all(isinstance(v, str) for v in note.data.values())
    if "reference" in note.data:
        assert REF.match(note.data["reference"]), note.data


def test_bill_payment_links_to_a_receipt_the_customer_can_open(ama):
    biller = Biller.objects.create(code="ECG_PREPAID", name="ECG Prepaid", category="electricity",
                                   rail_biller_code="ECG_PREPAID")
    p = payment_services.initiate_bill_payment(user=ama, biller=biller, account_ref="0123456789",
                                               amount_minor=50_00, network=Network.MTN,
                                               payer=ama.phone, idempotency_key="b1")
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)

    note = Notification.objects.get(user=ama, title="Payment successful")
    _assert_contract(note)
    assert note.data == {"type": "payment", "reference": p.reference, "status": "succeeded"}
    # The receipt endpoint the deep link opens works for the owner…
    c = APIClient()
    c.force_authenticate(user=ama)
    assert c.get(f"/api/v1/payments/{p.reference}").status_code == 200
    # …and nobody else.
    other = User.objects.create_user(phone="+233200000001")
    c.force_authenticate(user=other)
    assert c.get(f"/api/v1/payments/{p.reference}").status_code == 404


def test_wallet_notifications_follow_the_contract(ama):
    kofi = User.objects.create_user(phone="+233200000002", full_name="Kofi")
    _fund(kofi, 100_00)
    wallet.send_p2p(sender=kofi, recipient_phone=ama.phone, amount_minor=20_00)
    _assert_contract(Notification.objects.get(user=ama, title="Money received"))


def test_inbox_api_returns_link_data(ama):
    kofi = User.objects.create_user(phone="+233200000003", full_name="Kofi")
    _fund(kofi, 100_00)
    wallet.send_p2p(sender=kofi, recipient_phone=ama.phone, amount_minor=20_00)
    c = APIClient()
    c.force_authenticate(user=ama)
    rows = c.get("/api/v1/notifications").json()
    assert rows[0]["data"]["type"] == "wallet" and rows[0]["read"] is False
    assert c.post(f"/api/v1/notifications/{rows[0]['id']}/read").status_code in (200, 204)
    assert c.get("/api/v1/notifications").json()[0]["read"] is True
