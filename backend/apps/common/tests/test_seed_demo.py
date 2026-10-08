"""The demo seed keeps working as the code changes (the demo guide depends on it)."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.licensing.gate import _enabled_set

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _dev(settings):
    settings.DEBUG = True
    settings.ALLOW_MOCK_INTEGRATIONS = True
    yield
    _enabled_set.cache_clear()


def test_seed_demo_builds_everything_the_guide_uses(tmp_path):
    from apps.agents.models import Agent
    from apps.compliance.models import SuspiciousTransactionReport
    from apps.merchants.models import Dispute, Merchant, Settlement
    from apps.pricing.models import PriceRule
    out = tmp_path / "creds.md"
    call_command("seed_demo", out=str(out))
    assert "GRACEMTN" in out.read_text(encoding="utf-8")
    assert get_user_model().objects.filter(phone="+233244000201").exists()
    assert Merchant.objects.get(short_code="GRACEMTN").status == "approved"
    assert Merchant.objects.filter(status="submitted").count() == 1
    assert Agent.objects.filter(status="pending").count() == 1
    assert Settlement.objects.filter(status="awaiting_approval").count() == 1
    assert set(Dispute.objects.values_list("status", flat=True)) == {"open", "responded"}
    assert SuspiciousTransactionReport.objects.filter(status="draft").count() == 1
    assert PriceRule.objects.filter(status="pending").count() == 1
    with pytest.raises(CommandError, match="already loaded"):
        call_command("seed_demo")


def test_seed_demo_refuses_outside_dev(settings):
    settings.DEBUG = False
    settings.ALLOW_MOCK_INTEGRATIONS = False
    with pytest.raises(CommandError, match="development"):
        call_command("seed_demo")
