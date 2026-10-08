"""
Cross-cutting security controls found in the review (docs/SECURITY-REVIEW.md):
mock integrations are unreachable unless explicitly allowed; webhook endpoints only
answer for the configured provider and cap their payload.
"""

import json

import pytest
from django.test import Client
from rest_framework.test import APIClient

from apps.connectors import registry
from apps.rails.exceptions import UnknownRail
from apps.rails.registry import get_rail, reset_rail_cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _rail(settings):
    settings.RAIL_PROVIDER = "mock"
    reset_rail_cache()
    yield
    reset_rail_cache()


def test_mocks_unreachable_when_not_allowed(settings):
    assert get_rail("mock").name == "mock"                                  # dev: allowed
    assert registry.get_connector("transfer", "mock").is_mock

    settings.ALLOW_MOCK_INTEGRATIONS = False
    reset_rail_cache()
    with pytest.raises(UnknownRail):
        get_rail("mock")
    for category, key in (("transfer", "mock"), ("identity", "mock"), ("remittance", "mock"),
                          ("financial", "mock_insurance"), ("financial", "mock_lending")):
        with pytest.raises(registry.UnknownConnector):
            registry.get_connector(category, key)
    # Real (placeholder) connectors are unaffected.
    assert registry.get_connector("transfer", "zeepay").key == "zeepay"


def test_mock_remittance_webhook_is_404_when_mocks_disallowed(settings):
    from apps.connectors.remittance import MockRemittanceConnector
    body = json.dumps({"partner_ref": "R1", "recipient_phone": "+233244058519",
                       "amount_minor": 100_00, "currency": "GHS"}).encode()
    headers = {"HTTP_X_MOCK_SIGNATURE": MockRemittanceConnector.sign(body)}
    settings.ALLOW_MOCK_INTEGRATIONS = False
    r = Client().post("/api/v1/remittance/mock/webhook", body,
                      content_type="application/json", **headers)
    assert r.status_code == 404


def test_rail_webhook_only_answers_for_configured_rail_and_caps_body(settings):
    c = APIClient()
    assert c.post("/api/v1/rails/korba/webhook", b"{}", content_type="application/json").status_code == 404
    assert c.post("/api/v1/rails/nope/webhook", b"{}", content_type="application/json").status_code == 404
    big = b"{" + b"a" * (70 * 1024) + b"}"
    assert c.post("/api/v1/rails/mock/webhook", big, content_type="application/json").status_code == 413
    # The configured rail with a bad signature is rejected, not errored.
    r = c.post("/api/v1/rails/mock/webhook", b'{"provider_ref":"x"}', content_type="application/json")
    assert r.status_code == 401


def test_seed_marketplace_dev_refused_without_mocks(settings):
    from django.core.management import CommandError, call_command
    settings.ALLOW_MOCK_INTEGRATIONS = False
    with pytest.raises(CommandError):
        call_command("seed_marketplace", "--dev")
