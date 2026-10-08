"""
Settlement payout outcome tracking (webhook + poller), bank-account settlements, and
stuck-payout detection.

Money invariant checked throughout: postings sum to zero; a payout is either PAID
(money stays out) or FAILED (money back in the merchant's balance) — never both.
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.merchants import settlement
from apps.merchants.exceptions import SettlementError
from apps.merchants.models import Settlement, SettlementAccount
from apps.merchants.tasks import poll_settlement_payouts
from apps.notifications.models import Notification
from apps.payments import services as payment_services
from apps.rails.exceptions import RailError
from apps.rails.mock import MockRail
from apps.rails.types import RailStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def funded(approved_merchant):
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 500_00),
                        credit(accounts.merchant_payable(str(approved_merchant.id)), 500_00)])
    return approved_merchant


def _account(merchant, kind="momo", provider="mtn", number="+233244058519"):
    acct = SettlementAccount.objects.create(
        merchant=merchant, kind=kind, provider=provider, account_no=number,
        account_name="Ama Stores", name_check_status="matched", is_default=True)
    # Established account (outside the 72h "recently changed" approval window).
    SettlementAccount.objects.filter(pk=acct.pk).update(updated_at=timezone.now() - timedelta(days=5))
    acct.refresh_from_db()
    return acct


def _settle(merchant, acct, amount=200_00):
    return settlement.request_settlement(merchant=merchant, destination=acct,
                                         requested_by=merchant.owner, amount_minor=amount)


def _available(m):
    return settlement.available_balance_minor(m)


def _balanced():
    return sum(p.amount for p in Posting.objects.all()) == 0


def test_poller_marks_paid_and_tells_merchant(funded):
    s = _settle(funded, _account(funded))
    assert s.status == Settlement.Status.PROCESSING and s.rail == "mock" and s.rail_ref
    assert _available(funded) == 300_00

    assert poll_settlement_payouts() == {"resolved": 0, "stuck": 0}       # still pending
    MockRail.drive(s.rail_ref, RailStatus.SUCCEEDED)
    assert poll_settlement_payouts()["resolved"] == 1
    s.refresh_from_db()
    assert s.status == Settlement.Status.PAID and s.completed_at
    assert _available(funded) == 300_00 and _balanced()
    assert Notification.objects.filter(user=funded.owner, title="Settlement paid").exists()


def test_poller_reverses_failed_payout_once(funded):
    s = _settle(funded, _account(funded))
    MockRail.drive(s.rail_ref, RailStatus.FAILED)
    poll_settlement_payouts()
    s.refresh_from_db()
    assert s.status == Settlement.Status.FAILED and s.failure_code
    assert _available(funded) == 500_00 and _balanced()
    # Re-running (overlapping poll, late webhook) changes nothing.
    settlement.apply_payout_outcome(s, RailStatus.FAILED)
    settlement.apply_payout_outcome(s, RailStatus.SUCCEEDED)
    s.refresh_from_db()
    assert s.status == Settlement.Status.FAILED and _available(funded) == 500_00
    assert Notification.objects.filter(user=funded.owner, title="Settlement didn't go through").count() == 1


def _webhook(ref, status="succeeded"):
    body = json.dumps({"provider_ref": ref, "status": status}).encode()
    return payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)


def test_webhook_resolves_settlement_but_only_after_requery(funded):
    s = _settle(funded, _account(funded))
    # A (validly signed) callback claims success, but the rail still says pending:
    # the body is not trusted, so nothing changes.
    _webhook(s.rail_ref, "succeeded")
    s.refresh_from_db()
    assert s.status == Settlement.Status.PROCESSING

    MockRail.drive(s.rail_ref, RailStatus.SUCCEEDED)
    _webhook(s.rail_ref, "succeeded")
    s.refresh_from_db()
    assert s.status == Settlement.Status.PAID and _balanced()


def test_forged_webhook_is_rejected(funded):
    from apps.rails.exceptions import WebhookVerificationError
    s = _settle(funded, _account(funded))
    MockRail.drive(s.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": s.rail_ref, "status": "succeeded"}).encode()
    with pytest.raises(WebhookVerificationError):
        payment_services.handle_webhook("mock", {"X-Mock-Signature": "nope"}, body)
    s.refresh_from_db()
    assert s.status == Settlement.Status.PROCESSING


def test_bank_settlement_uses_bank_payout(funded):
    acct = _account(funded, kind="bank", provider="gcb", number="1234567890123")
    s = _settle(funded, acct)
    assert s.status == Settlement.Status.PROCESSING
    assert MockRail._records[s.rail_ref][0] == "bank_payout"
    MockRail.drive(s.rail_ref, RailStatus.SUCCEEDED)
    poll_settlement_payouts()
    s.refresh_from_db()
    assert s.status == Settlement.Status.PAID


def test_bank_settlement_refused_up_front_when_rail_cannot_pay_banks(funded, monkeypatch):
    monkeypatch.setattr(MockRail, "supports_bank_payout", False)
    acct = _account(funded, kind="bank", provider="GCB", number="1234567890123")
    with pytest.raises(SettlementError, match="Bank settlements aren't available"):
        _settle(funded, acct)
    assert not Settlement.objects.exists()
    assert _available(funded) == 500_00 and _balanced()


def test_payout_that_never_left_is_reversed(funded, monkeypatch):
    def boom(self, req):
        raise RailError("network code unsupported")
    monkeypatch.setattr(MockRail, "payout", boom)
    s = _settle(funded, _account(funded))
    assert s.status == Settlement.Status.FAILED and s.failure_code == "not_sent"
    assert _available(funded) == 500_00 and _balanced()


def test_invalid_momo_network_refused(funded):
    with pytest.raises(SettlementError, match="network"):
        _settle(funded, _account(funded, provider="glo"))


def test_stuck_payouts_are_flagged(funded):
    s1 = _settle(funded, _account(funded), amount=100_00)
    s2 = _settle(funded, _account(funded, number="+233244058520"), amount=100_00)
    assert not settlement.stuck_settlements().exists()
    # s1: partner hasn't confirmed for over a day. s2: crash before we got a reference.
    Settlement.objects.filter(pk=s1.pk).update(updated_at=timezone.now() - timedelta(hours=25))
    Settlement.objects.filter(pk=s2.pk).update(rail_ref="", updated_at=timezone.now() - timedelta(minutes=11))
    assert set(settlement.stuck_settlements()) == {s1, s2}
    assert poll_settlement_payouts()["stuck"] == 2

    # The poller never blindly resolves the reference-less one.
    MockRail.drive(s1.rail_ref, RailStatus.SUCCEEDED)
    poll_settlement_payouts()
    assert list(settlement.stuck_settlements()) == [s2]


def test_admin_sees_stuck_payouts_and_can_recheck(funded, staff):
    from apps.portal.tests.helpers import PASSWORD, login_verified
    staff.set_password(PASSWORD)
    staff.save()
    s = _settle(funded, _account(funded))
    Settlement.objects.filter(pk=s.pk).update(updated_at=timezone.now() - timedelta(hours=30))
    c = login_verified(staff.phone)
    assert b"stuck with the payment partner" in c.get("/dashboard/admin/").content
    page = c.get("/dashboard/admin/settlements/")
    assert s.rail_ref.encode() in page.content and b"Re-check" in page.content

    MockRail.drive(s.rail_ref, RailStatus.SUCCEEDED)
    c.post("/dashboard/admin/settlements/", {"settlement": str(s.id), "action": "recheck"})
    s.refresh_from_db()
    assert s.status == Settlement.Status.PAID


def test_settlement_account_form_validates_network():
    from apps.portal.forms import SettlementAccountForm
    bad = SettlementAccountForm({"kind": "momo", "provider": "Glo", "account_no": "1", "account_name": "x"})
    assert not bad.is_valid()
    good = SettlementAccountForm({"kind": "momo", "provider": "MTN", "account_no": "1", "account_name": "x"})
    assert good.is_valid() and good.cleaned_data["provider"] == "mtn"
    bank = SettlementAccountForm({"kind": "bank", "provider": "gcb", "account_no": "1", "account_name": "x"})
    assert bank.is_valid() and bank.cleaned_data["provider"] == "GCB"


def test_clearing_account_follows_the_rail_used(funded, settings):
    """If RAIL_PROVIDER changes while a payout is in flight, the reversal still hits the
    clearing account of the rail that carried it."""
    s = _settle(funded, _account(funded))
    settings.RAIL_PROVIDER = "korba"
    MockRail.drive(s.rail_ref, RailStatus.FAILED)
    settlement.refresh_settlement(s)
    s.refresh_from_db()
    assert s.status == Settlement.Status.FAILED
    assert natural_balance_of(accounts.partner_clearing("mock")) == 500_00
    assert _available(funded) == 500_00 and _balanced()
