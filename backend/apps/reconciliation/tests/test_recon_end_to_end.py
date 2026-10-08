"""
End-to-end reconciliation: real payments through the mock rail, the rail's settlement
report from MockRail.settled_transactions(), and the daily task classifying the result.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.licensing.gate import _enabled_set
from apps.payments import services as payment_services
from apps.payments.models import Biller, Payment
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.reconciliation.models import ReconItem, ReconRun
from apps.reconciliation.tasks import run_daily_reconciliation

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def ecg(db):
    return Biller.objects.create(code="ECG", name="ECG", category="electricity",
                                 rail_biller_code="ecg")


def _paid_bill(ecg, key, amount_minor):
    user, _ = User.objects.get_or_create(phone="+233244058519")
    p = payment_services.initiate_bill_payment(
        user=user, biller=ecg, account_ref="P1", amount_minor=amount_minor,
        network=Network.MTN, payer="+233244058519", idempotency_key=key,
    )
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    p.refresh_from_db()
    assert p.status == Payment.Status.SUCCEEDED
    return p


def test_clean_day_reconciles_with_no_breaks(ecg):
    _paid_bill(ecg, "r1", 50_00)
    _paid_bill(ecg, "r2", 120_00)

    result = run_daily_reconciliation(rail="mock", date=str(timezone.now().date()))

    # Both collections matched; bill deliveries are outbound and not in this report.
    assert result["matched"] == 2
    assert result["breaks"] == 0


def test_breaks_are_found_and_recorded(ecg):
    _paid_bill(ecg, "r1", 50_00)                       # matched
    MockRail.add_settled("MOCK-PARTNER-ONLY", 999)      # rail has it, we don't
    Payment.objects.create(                             # we have it, rail doesn't
        reference="SP-OURSONLY01", purpose=Payment.Purpose.BILL,
        status=Payment.Status.SUCCEEDED, amount_minor=700, total_minor=700,
        network="mtn", rail="mock", rail_ref="MOCK-OURS-ONLY",
        completed_at=timezone.now(),
    )

    result = run_daily_reconciliation(rail="mock", date=str(timezone.now().date()))
    assert result["matched"] == 1
    assert result["breaks"] == 2

    run = ReconRun.objects.latest("created_at")
    items = {i.rail_ref: i.kind for i in run.items.all()}
    assert items == {
        "MOCK-PARTNER-ONLY": ReconItem.Kind.THEIRS_ONLY,
        "MOCK-OURS-ONLY": ReconItem.Kind.OURS_ONLY,
    }
