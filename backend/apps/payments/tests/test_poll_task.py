"""Pending-payment polling task (the safety net for lost webhooks)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.licensing.gate import _enabled_set
from apps.payments import services
from apps.payments.models import Biller, Payment
from apps.payments.tasks import poll
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    reset_rail_cache()
    MockRail.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


def test_poll_resolves_a_stuck_pending_payment(db):
    user = User.objects.create_user(phone="+233244058519")
    biller = Biller.objects.create(code="ECG", name="ECG", category="electricity",
                                   rail_biller_code="ecg")
    p = services.initiate_bill_payment(
        user=user, biller=biller, account_ref="P1", amount_minor=50_00,
        network=Network.MTN, payer="+233244058519", idempotency_key="poll-1",
    )
    # Make it look old enough to poll, and have the rail now report success.
    Payment.objects.filter(pk=p.pk).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)

    result = poll(older_than_minutes=3)
    assert result["resolved"] == 1
    p.refresh_from_db()
    assert p.status == Payment.Status.SUCCEEDED
