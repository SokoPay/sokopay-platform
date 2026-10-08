"""SMS provider selection and OTP delivery through the configured provider."""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.notifications.sms import get_sms_provider
from apps.notifications.sms.console import ConsoleSmsProvider
from apps.notifications.sms.registry import reset_sms_cache

pytestmark = pytest.mark.django_db
PHONE = "+233244058519"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.SMS_PROVIDER = "console"
    reset_sms_cache()
    ConsoleSmsProvider.reset()
    cache.clear()
    yield
    reset_sms_cache()
    cache.clear()


def test_registry_selects_provider(settings):
    assert get_sms_provider().name == "console"
    settings.SMS_PROVIDER = "hubtel"
    reset_sms_cache()
    assert get_sms_provider().name == "hubtel"


def test_otp_request_sends_sms_with_the_code():
    client = APIClient()
    resp = client.post("/api/v1/auth/otp/request", {"phone": PHONE}, format="json")
    assert resp.status_code == 200
    # Exactly one SMS was "sent", to the right number, containing a 6-digit code.
    assert len(ConsoleSmsProvider.sent) == 1
    to, message = ConsoleSmsProvider.sent[0]
    assert to == PHONE
    assert "SokoPay code is" in message
    import re
    assert re.search(r"\b\d{6}\b", message)
