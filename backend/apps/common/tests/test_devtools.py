"""The dev-only mock partner completes a pending MoMo payment like a real approval would."""

import pytest
from django.contrib.auth import get_user_model

from apps.common import devtools
from apps.licensing.gate import _enabled_set
from apps.merchants import checkout, onboarding
from apps.payments.models import Payment
from apps.rails.types import Network

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demo(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"
    settings.ALLOW_MOCK_INTEGRATIONS = True
    settings.DEBUG = True
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


def test_approve_and_disabled_outside_dev(settings):
    owner = get_user_model().objects.create_user(phone="+233200000010")
    shop = onboarding.create_merchant(owner=owner, legal_name="Shop Ltd", business_type="registered")
    for step in (onboarding.submit_for_review, onboarding.begin_review, onboarding.approve):
        step(shop)
    p = checkout.initiate_merchant_charge(merchant=shop, amount_minor=50_00, network=Network.MTN, payer="+233244058519")
    assert devtools._settle("payment", str(p.pk), devtools.RailStatus.SUCCEEDED) == p.reference
    p.refresh_from_db()
    assert p.status == Payment.Status.SUCCEEDED
    settings.DEBUG = False
    from django.http import Http404
    from django.test import RequestFactory
    with pytest.raises(Http404):
        devtools.mock_partner(RequestFactory().get("/dev/mock-partner/"))
