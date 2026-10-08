"""
End-to-end payment flow tests — the most important tests in this milestone.

They exercise the full path (initiate → rail collect → webhook → ledger) and the
failure modes that a payments system must get right: duplicate callbacks, forged or
disagreeing callbacks, and a biller failing after we have taken the money.
"""

import json

import pytest

from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import natural_balance_of
from apps.licensing.gate import _enabled_set
from apps.payments import services
from apps.payments.models import Biller, Payment, RailEvent
from apps.rails.exceptions import WebhookVerificationError
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus

User = pytest.importorskip("django.contrib.auth").get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.RAIL_WEBHOOK_SECRET = "test-secret"
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    settings.DEFAULT_CURRENCY = "GHS"
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def user(db):
    return User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")


@pytest.fixture
def ecg(db):
    return Biller.objects.create(
        code="ECG_PREPAID", name="ECG Prepaid", category="electricity",
        rail_biller_code="ecg_prepaid", supports_lookup=True,
    )


def _signed_webhook(provider_ref: str, status: str):
    body = json.dumps({"provider_ref": provider_ref, "status": status}).encode()
    headers = {"X-Mock-Signature": MockRail().sign(body)}
    return headers, body


def _bill(user, ecg, amount_minor=10_000):
    return services.initiate_bill_payment(
        user=user, biller=ecg, account_ref="P123456789",
        amount_minor=amount_minor, network=Network.MTN, payer="+233244058519",
        idempotency_key="order-1",
    )


# --- happy path -------------------------------------------------------------
def test_bill_payment_happy_path(user, ecg):
    payment = _bill(user, ecg)
    assert payment.status == Payment.Status.PENDING
    assert payment.fee_minor == 50           # flat bill fee
    assert payment.total_minor == 10_050

    # Customer approves on their phone; the rail now reports success.
    MockRail.drive(payment.rail_ref, RailStatus.SUCCEEDED)
    headers, body = _signed_webhook(payment.rail_ref, "succeeded")
    services.handle_webhook("mock", headers, body)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.SUCCEEDED
    # Ledger: we kept the fee, the biller was delivered, books balance to zero.
    assert natural_balance_of(accounts.fee_revenue()) == 50
    assert natural_balance_of(accounts.biller_payable("ECG_PREPAID")) == 0
    assert sum(p.amount for p in Posting.objects.all()) == 0


# --- idempotency: a duplicated callback must not double-post ----------------
def test_duplicate_webhook_does_not_double_charge(user, ecg):
    payment = _bill(user, ecg)
    MockRail.drive(payment.rail_ref, RailStatus.SUCCEEDED)
    headers, body = _signed_webhook(payment.rail_ref, "succeeded")

    services.handle_webhook("mock", headers, body)
    postings_after_first = Posting.objects.count()
    services.handle_webhook("mock", headers, body)  # the rail retried the callback

    assert Posting.objects.count() == postings_after_first
    assert natural_balance_of(accounts.fee_revenue()) == 50   # not 100


# --- security: forged callback rejected; genuine status always re-queried ---
def test_bad_signature_is_rejected_and_recorded(user, ecg):
    payment = _bill(user, ecg)
    body = json.dumps({"provider_ref": payment.rail_ref, "status": "succeeded"}).encode()
    headers = {"X-Mock-Signature": "forged"}

    with pytest.raises(WebhookVerificationError):
        services.handle_webhook("mock", headers, body)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING      # nothing changed
    assert RailEvent.objects.filter(signature_ok=False).exists()
    assert Posting.objects.count() == 0                  # no money moved


def test_callback_claims_success_but_requery_says_failed(user, ecg):
    """A signed callback says 'succeeded', but the authoritative re-query says failed.
    We must trust the re-query and fail the payment — defeats forged/replayed success."""
    payment = _bill(user, ecg)
    MockRail.drive(payment.rail_ref, RailStatus.FAILED)       # the truth at the rail
    headers, body = _signed_webhook(payment.rail_ref, "succeeded")  # the lie in the body

    services.handle_webhook("mock", headers, body)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.failure_code == "rail_declined"
    assert Posting.objects.count() == 0


# --- biller fails after we collected: auto-refund-pending, books still balance ---
def test_biller_failure_parks_refund_and_balances(user, ecg):
    MockRail.set_bill_status(RailStatus.FAILED)              # biller will decline delivery
    payment = _bill(user, ecg)
    MockRail.drive(payment.rail_ref, RailStatus.SUCCEEDED)   # but the collection succeeded
    headers, body = _signed_webhook(payment.rail_ref, "succeeded")

    services.handle_webhook("mock", headers, body)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.failure_code == "biller_failed"
    # We owe the customer the full amount back; we did not keep the fee.
    assert natural_balance_of(accounts.refund_payable()) == payment.total_minor
    assert natural_balance_of(accounts.fee_revenue()) == 0
    assert natural_balance_of(accounts.biller_payable("ECG_PREPAID")) == 0
    assert sum(p.amount for p in Posting.objects.all()) == 0


# --- initiation idempotency and validation ----------------------------------
def test_initiation_is_idempotent_on_key(user, ecg):
    p1 = _bill(user, ecg)
    p2 = _bill(user, ecg)   # same idempotency_key
    assert p1.id == p2.id
    assert Payment.objects.count() == 1


def test_amount_outside_biller_range_is_rejected(user, ecg):
    ecg.min_minor = 1_000
    ecg.save()
    with pytest.raises(services.PaymentError):
        services.initiate_bill_payment(
            user=user, biller=ecg, account_ref="P1", amount_minor=100,  # GH₵1 < min
            network=Network.MTN, payer="+233244058519", idempotency_key="k-small",
        )
