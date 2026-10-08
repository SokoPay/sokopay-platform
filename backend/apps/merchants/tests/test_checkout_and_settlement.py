"""
Merchant checkout (collection → ledger) and settlement (maker-checker), plus the
licence gate blocking these PSP-Medium activities under a PSP-Standard licence.
"""

import json

import pytest

from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import natural_balance_of
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.merchants import checkout, settlement
from apps.merchants.exceptions import ApprovalError, SettlementError
from apps.merchants.models import Settlement
from apps.payments import services as payment_services
from apps.payments.models import Payment
from apps.rails.mock import MockRail
from apps.rails.types import Network, RailStatus

pytestmark = pytest.mark.django_db


def _charge(merchant, amount_minor=100_00):
    return checkout.initiate_merchant_charge(
        merchant=merchant, amount_minor=amount_minor, network=Network.MTN,
        payer="+233244058519", idempotency_key=f"charge-{amount_minor}",
    )


def _complete(payment):
    MockRail.drive(payment.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": payment.rail_ref, "status": "succeeded"}).encode()
    headers = {"X-Mock-Signature": MockRail().sign(body)}
    payment_services.handle_webhook("mock", headers, body)


# --- licence gate -----------------------------------------------------------
def test_checkout_blocked_under_psp_standard(settings, approved_merchant):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"   # aggregation not permitted here
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        _charge(approved_merchant)


# --- checkout → ledger ------------------------------------------------------
def test_merchant_collection_credits_payable_net_of_fee(approved_merchant):
    payment = _charge(approved_merchant, amount_minor=100_00)  # GH₵100.00
    assert payment.status == Payment.Status.PENDING
    # MDR 1.5% of GH₵100 = GH₵1.50.
    assert payment.fee_minor == 150

    _complete(payment)
    payment.refresh_from_db()
    assert payment.status == Payment.Status.SUCCEEDED

    payable = accounts.merchant_payable(str(approved_merchant.id))
    assert natural_balance_of(payable) == 100_00 - 150          # net to merchant
    assert natural_balance_of(accounts.fee_revenue()) == 150     # SokoPay keeps the MDR
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_unapproved_merchant_cannot_charge(owner):
    from apps.merchants import onboarding
    m = onboarding.create_merchant(owner=owner, legal_name="New", business_type="registered")
    with pytest.raises(Exception):
        _charge(m)


# --- settlement + maker-checker --------------------------------------------
def _fund(merchant, amount_minor):
    """Put money in the merchant's payable by completing a collection."""
    p = _charge(merchant, amount_minor=amount_minor)
    _complete(p)
    return p


def test_small_settlement_executes_immediately(approved_merchant, settlement_account, owner):
    # Age the destination past the 72h "recently changed" window so a small
    # settlement to an established account executes without maker-checker.
    from datetime import timedelta

    from django.utils import timezone

    from apps.merchants.models import SettlementAccount
    SettlementAccount.objects.filter(pk=settlement_account.pk).update(
        updated_at=timezone.now() - timedelta(days=4)
    )
    settlement_account.refresh_from_db()

    _fund(approved_merchant, 100_00)
    available = settlement.available_balance_minor(approved_merchant)

    s = settlement.request_settlement(
        merchant=approved_merchant, destination=settlement_account,
        requested_by=owner, amount_minor=available,
    )
    s.refresh_from_db()
    assert s.status == Settlement.Status.PROCESSING          # no approval needed
    # Payable drained by the settled amount.
    assert settlement.available_balance_minor(approved_merchant) == 0
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_refused_payout_returns_money_to_merchant(approved_merchant, settlement_account, owner,
                                                  monkeypatch):
    """A definitive partner rejection reverses the settlement; the merchant can retry."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.merchants.models import SettlementAccount
    from apps.rails.types import RailResult
    SettlementAccount.objects.filter(pk=settlement_account.pk).update(
        updated_at=timezone.now() - timedelta(days=4))
    settlement_account.refresh_from_db()
    _fund(approved_merchant, 100_00)
    available = settlement.available_balance_minor(approved_merchant)

    monkeypatch.setattr(MockRail, "payout", lambda self, req: RailResult(
        status=RailStatus.FAILED, provider_ref="REJ-1", failure_code="rejected:400"))
    s = settlement.request_settlement(merchant=approved_merchant, destination=settlement_account,
                                      requested_by=owner, amount_minor=available)
    s.refresh_from_db()
    assert s.status == Settlement.Status.FAILED and s.rail_ref == "REJ-1"
    assert settlement.available_balance_minor(approved_merchant) == available
    assert sum(p.amount for p in Posting.objects.all()) == 0

    # An UNKNOWN outcome (e.g. timeout) must NOT hand the money back.
    monkeypatch.setattr(MockRail, "payout", lambda self, req: RailResult(
        status=RailStatus.UNKNOWN, provider_ref="UNK-1"))
    s2 = settlement.request_settlement(merchant=approved_merchant, destination=settlement_account,
                                       requested_by=owner, amount_minor=available)
    s2.refresh_from_db()
    assert s2.status == Settlement.Status.PROCESSING
    assert settlement.available_balance_minor(approved_merchant) == 0


def test_large_settlement_requires_approval_by_a_different_staff(
    approved_merchant, settlement_account, owner, staff, staff2
):
    # Fund above the GH₵50,000 approval threshold.
    _fund(approved_merchant, 60_000_00)
    available = settlement.available_balance_minor(approved_merchant)

    s = settlement.request_settlement(
        merchant=approved_merchant, destination=settlement_account,
        requested_by=staff, amount_minor=available,
    )
    assert s.status == Settlement.Status.AWAITING_APPROVAL   # held for a checker

    # The maker cannot approve their own request.
    with pytest.raises(ApprovalError):
        settlement.approve_settlement(s, checker=staff)

    # A different staff member approves → it executes.
    settlement.approve_settlement(s, checker=staff2)
    s.refresh_from_db()
    assert s.status == Settlement.Status.PROCESSING
    assert s.approved_by_id == staff2.id


def test_cannot_settle_to_unverified_account(approved_merchant, owner):
    from apps.merchants.models import SettlementAccount
    _fund(approved_merchant, 100_00)
    acct = SettlementAccount.objects.create(
        merchant=approved_merchant, kind="momo", provider="mtn",
        account_no="+233200000009", account_name="Ama", name_check_status="pending",
    )
    with pytest.raises(SettlementError):
        settlement.request_settlement(
            merchant=approved_merchant, destination=acct, requested_by=owner,
        )


def test_cannot_settle_more_than_available(approved_merchant, settlement_account, owner):
    _fund(approved_merchant, 100_00)
    available = settlement.available_balance_minor(approved_merchant)
    with pytest.raises(SettlementError):
        settlement.request_settlement(
            merchant=approved_merchant, destination=settlement_account,
            requested_by=owner, amount_minor=available + 1,
        )
