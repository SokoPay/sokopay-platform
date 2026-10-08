"""
Consumer wallet: funding (MoMo → wallet), P2P transfers, history, and the licence
gate (DEMI). Books always balance to zero.
"""

import json

import pytest
from django.contrib.auth import get_user_model

from apps.ledger.models import Posting
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.payments import services as payment_services
from apps.payments.models import Payment
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import services
from apps.wallet.exceptions import WalletError

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.DEFAULT_CURRENCY = "GHS"
    reset_rail_cache()
    MockRail.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def ama(db):
    return User.objects.create_user(phone="+233244058519", full_name="Ama")


@pytest.fixture
def kofi(db):
    return User.objects.create_user(phone="+233209998888", full_name="Kofi Owusu")


def _fund(user, amount_minor):
    p = services.initiate_funding(user=user, amount_minor=amount_minor, network=Network.MTN,
                                  payer=user.phone, idempotency_key=f"fund-{user.id}-{amount_minor}")
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    return p


def test_funding_credits_wallet(ama):
    p = _fund(ama, 200_00)
    p.refresh_from_db()
    assert p.status == Payment.Status.SUCCEEDED
    assert p.purpose == Payment.Purpose.WALLET_FUND
    assert services.balance(ama) == 200_00
    assert sum(x.amount for x in Posting.objects.all()) == 0


def test_p2p_moves_money_between_wallets(ama, kofi):
    _fund(ama, 200_00)
    result = services.send_p2p(sender=ama, recipient_phone=kofi.phone, amount_minor=50_00)
    assert result["new_balance_minor"] == 150_00
    assert result["recipient_name"] == "Kofi O."          # recognisable, not the full name

    assert services.balance(kofi) == 50_00
    assert sum(x.amount for x in Posting.objects.all()) == 0


def test_p2p_to_unknown_number_is_refused_and_creates_nothing(ama):
    _fund(ama, 200_00)
    with pytest.raises(WalletError):
        services.send_p2p(sender=ama, recipient_phone="+233200000000", amount_minor=50_00)
    assert not User.objects.filter(phone="+233200000000").exists()
    assert services.balance(ama) == 200_00
    assert services.lookup_recipient("+233200000000") == {"found": False, "name": ""}


def test_cannot_send_more_than_balance(ama, kofi):
    _fund(ama, 30_00)
    with pytest.raises(WalletError):
        services.send_p2p(sender=ama, recipient_phone=kofi.phone, amount_minor=50_00)


def test_cannot_send_to_self(ama):
    _fund(ama, 100_00)
    with pytest.raises(WalletError):
        services.send_p2p(sender=ama, recipient_phone=ama.phone, amount_minor=10_00)


def test_history_shows_in_and_out(ama, kofi):
    _fund(ama, 100_00)
    services.send_p2p(sender=ama, recipient_phone=kofi.phone, amount_minor=40_00)
    rows = services.history(ama)
    directions = {r["direction"] for r in rows}
    assert "in" in directions and "out" in directions


def test_wallet_blocked_under_psp_standard(settings, ama):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        services.initiate_funding(user=ama, amount_minor=100_00, network=Network.MTN)
