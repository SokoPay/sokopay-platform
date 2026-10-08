"""
Interoperable transfers from a SokoPay wallet (DEMI): to MoMo via the partner, to
banks/fintechs via connectors. Covers reserve → settle, reserve → refund, pending
resolution by the poller, placeholders, bad recipients, and the licence gate.
The ledger must balance to zero in every case.
"""

import json

import pytest
from django.contrib.auth import get_user_model

from apps.connectors.transfers import MockTransferConnector
from apps.connectors.types import DestinationType, TransferDestination
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import natural_balance_of
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.payments import services as payment_services
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import interop
from apps.wallet import services as wallet
from apps.wallet.models import ExternalTransfer

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.INTEROP_ROUTES = {"momo": "rail", "bank": "mock", "wallet": "mock"}
    reset_rail_cache()
    MockRail.reset()
    MockTransferConnector.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def ama(db):
    user = User.objects.create_user(phone="+233244058519", full_name="Ama")
    p = wallet.initiate_funding(user=user, amount_minor=500_00, network=Network.MTN,
                                payer=user.phone, idempotency_key="fund-ama")
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    assert wallet.balance(user) == 500_00
    return user


MOMO = TransferDestination(DestinationType.MOMO, "telecel", "+233201112222")
BANK = TransferDestination(DestinationType.BANK, "GCB", "1234567890123")
ZEEPAY = TransferDestination(DestinationType.WALLET, "zeepay", "+233209998888")


def _ledger_balanced():
    return sum(p.amount for p in Posting.objects.all()) == 0


def test_name_enquiry(ama):
    assert interop.name_enquiry(MOMO).account_name == "TEST WALLET 2222"
    assert interop.name_enquiry(BANK).account_name == "TEST RECIPIENT 0123"


def test_momo_transfer_pending_then_settled_by_poller(ama):
    t = interop.send_external(sender=ama, destination=MOMO, amount_minor=100_00,
                              idempotency_key="x1")
    # Partner disbursement is asynchronous: money is reserved, transfer pending.
    assert t.status == ExternalTransfer.Status.PENDING
    assert wallet.balance(ama) == 400_00
    assert natural_balance_of(accounts.interop_in_flight()) == 100_00

    MockRail.drive(t.provider_ref, RailStatus.SUCCEEDED)
    assert interop.resolve_pending()["resolved"] == 1
    t.refresh_from_db()
    assert t.status == ExternalTransfer.Status.SUCCEEDED
    assert natural_balance_of(accounts.interop_in_flight()) == 0
    assert _ledger_balanced()


def test_bank_transfer_succeeds_immediately(ama):
    t = interop.send_external(sender=ama, destination=BANK, amount_minor=50_00)
    assert t.status == ExternalTransfer.Status.SUCCEEDED
    assert t.account_name == "TEST RECIPIENT 0123"
    assert wallet.balance(ama) == 450_00
    assert _ledger_balanced()


def test_declined_transfer_is_fully_refunded(ama):
    MockTransferConnector.script(RailStatus.FAILED)
    t = interop.send_external(sender=ama, destination=ZEEPAY, amount_minor=80_00)
    assert t.status == ExternalTransfer.Status.FAILED
    assert wallet.balance(ama) == 500_00                     # nothing lost
    assert natural_balance_of(accounts.interop_in_flight()) == 0
    assert _ledger_balanced()


def test_placeholder_route_refused_before_money_moves(settings, ama):
    settings.INTEROP_ROUTES = {"momo": "rail", "bank": "ghipss_gip", "wallet": "mock"}
    with pytest.raises(interop.TransferError):
        interop.send_external(sender=ama, destination=BANK, amount_minor=10_00)
    assert wallet.balance(ama) == 500_00
    assert ExternalTransfer.objects.count() == 0


def test_unknown_recipient_refused(ama):
    nobody = TransferDestination(DestinationType.BANK, "GCB", "9999990000")
    with pytest.raises(interop.TransferError):
        interop.send_external(sender=ama, destination=nobody, amount_minor=10_00)
    assert wallet.balance(ama) == 500_00


def test_insufficient_balance(ama):
    with pytest.raises(interop.TransferError):
        interop.send_external(sender=ama, destination=BANK, amount_minor=600_00)


def test_idempotent(ama):
    a = interop.send_external(sender=ama, destination=BANK, amount_minor=10_00,
                              idempotency_key="same")
    b = interop.send_external(sender=ama, destination=BANK, amount_minor=10_00,
                              idempotency_key="same")
    assert a.id == b.id
    assert wallet.balance(ama) == 490_00


def test_blocked_without_demi(settings, ama):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_ENHANCED"
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        interop.send_external(sender=ama, destination=BANK, amount_minor=10_00)
