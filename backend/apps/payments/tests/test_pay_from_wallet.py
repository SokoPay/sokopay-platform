"""
Paying bills, airtime and data from the SokoPay wallet (DEMI): immediate completion,
refund straight back to the wallet if the biller fails, balance and limit checks, and
the licence gate.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.kyc.exceptions import LimitExceeded
from apps.ledger.models import Posting
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.payments import services
from apps.payments.models import Biller, Payment
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import services as wallet

User = get_user_model()
pytestmark = pytest.mark.django_db


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


@pytest.fixture
def ecg(db):
    return Biller.objects.create(code="ECG_PREPAID", name="ECG Prepaid",
                                 category="electricity", rail_biller_code="ecg_prepaid")


def _topup(user, amount_minor, key):
    p = wallet.initiate_funding(user=user, amount_minor=amount_minor, network=Network.MTN,
                                payer=user.phone, idempotency_key=key)
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)


@pytest.fixture
def ama(db):
    user = User.objects.create_user(phone="+233244058519")
    _topup(user, 300_00, "fund")
    return user


def _bill(user, biller, amount_minor, key="w1"):
    return services.initiate_bill_payment(
        user=user, biller=biller, account_ref="P1", amount_minor=amount_minor,
        source=Payment.Source.WALLET, idempotency_key=key,
    )


def test_wallet_bill_payment_completes_immediately(ama, ecg):
    p = _bill(ama, ecg, 100_00)
    assert p.status == Payment.Status.SUCCEEDED          # no MoMo prompt to wait for
    assert p.funding_source == Payment.Source.WALLET
    assert wallet.balance(ama) == 300_00 - 100_50        # amount + GH₵0.50 fee
    assert sum(x.amount for x in Posting.objects.all()) == 0


def test_biller_failure_refunds_straight_to_wallet(ama, ecg):
    MockRail.set_bill_status(RailStatus.FAILED)
    p = _bill(ama, ecg, 100_00)
    assert p.status == Payment.Status.REFUNDED
    assert wallet.balance(ama) == 300_00                 # every pesewa back, fee included
    assert sum(x.amount for x in Posting.objects.all()) == 0


def test_insufficient_wallet_balance(ama, ecg):
    with pytest.raises(services.PaymentError):
        _bill(ama, ecg, 400_00)
    assert wallet.balance(ama) == 300_00


def test_wallet_payments_respect_kyc_limits(ama, ecg):
    _topup(ama, 3_000_00, "fund-2")          # GH₵3,300 in the wallet — enough to pay
    with pytest.raises(LimitExceeded):       # …but the Minimum tier allows GH₵3,000 per transaction
        _bill(ama, ecg, 3_001_00, key="big")
    assert wallet.balance(ama) == 3_300_00


def test_wallet_source_needs_demi(settings, ama, ecg):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_ENHANCED"
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        _bill(ama, ecg, 10_00)


def test_momo_source_still_requires_network_and_payer(ama, ecg):
    with pytest.raises(services.PaymentError):
        services.initiate_bill_payment(user=ama, biller=ecg, account_ref="P1",
                                       amount_minor=10_00)


def test_api_wallet_source(ama, ecg):
    client = APIClient()
    client.force_authenticate(user=ama)
    r = client.post("/api/v1/payments/bill", {
        "biller_code": "ECG_PREPAID", "account": "P1", "amount": "20.00", "source": "wallet",
    }, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["status"] == "succeeded" and r.json()["funding_source"] == "wallet"

    # MoMo without network/payer is a validation error, not a crash.
    r = client.post("/api/v1/payments/bill", {
        "biller_code": "ECG_PREPAID", "account": "P1", "amount": "20.00",
    }, format="json")
    assert r.status_code == 400
