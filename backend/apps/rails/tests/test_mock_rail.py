"""Tests for the rail registry and the MockRail (signature + status behaviour)."""

import json

import pytest
from django.test import override_settings

from apps.common.money import Money
from apps.rails.mock import MockRail
from apps.rails.registry import get_rail, reset_rail_cache
from apps.rails.types import CollectionRequest, Network, RailStatus


@pytest.fixture(autouse=True)
def _reset():
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    yield
    reset_rail_cache()


def _collection():
    return CollectionRequest(
        amount=Money(10_000, "GHS"), network=Network.MTN, payer="+233244058519",
        reference="SP-TEST000001", narrative="test", idempotency_key="k1",
    )


@override_settings(RAIL_PROVIDER="mock")
def test_registry_returns_configured_rail():
    assert get_rail().name == "mock"


def test_collect_starts_pending_then_can_be_driven():
    rail = MockRail()
    res = rail.collect(_collection())
    assert res.status == RailStatus.PENDING
    assert rail.get_status(res.provider_ref).status == RailStatus.PENDING

    MockRail.drive(res.provider_ref, RailStatus.SUCCEEDED)
    assert rail.get_status(res.provider_ref).status == RailStatus.SUCCEEDED


@override_settings(RAIL_WEBHOOK_SECRET="top-secret")
def test_webhook_signature_valid_and_invalid():
    rail = MockRail()
    body = json.dumps({"provider_ref": "MOCK-abc", "status": "succeeded"}).encode()

    good = rail.verify_and_parse_webhook({"x-mock-signature": rail.sign(body)}, body)
    assert good.signature_ok is True
    assert good.status == RailStatus.SUCCEEDED
    assert good.provider_ref == "MOCK-abc"

    bad = rail.verify_and_parse_webhook({"x-mock-signature": "not-the-signature"}, body)
    assert bad.signature_ok is False


def test_unknown_status_is_normalised():
    rail = MockRail()
    body = json.dumps({"provider_ref": "X", "status": "weird"}).encode()
    event = rail.verify_and_parse_webhook({"x-mock-signature": rail.sign(body)}, body)
    assert event.status == RailStatus.UNKNOWN
