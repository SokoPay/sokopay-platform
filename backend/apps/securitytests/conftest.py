"""Shared fixtures for the customer-app security suite: two funded customers, an agent, a merchant."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.licensing.gate import _enabled_set
from apps.payments import services as payment_services
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import services as wallet

User = get_user_model()
PIN = "482915"
OTHER_PIN = "739164"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.RAIL_PROVIDER = "mock"
    settings.KYC_IDENTITY_PROVIDER = "mock"
    settings.SMS_PROVIDER = "console"
    settings.FIELD_ENCRYPTION_KEY = "test-field-key"
    _enabled_set.cache_clear()
    reset_rail_cache()
    MockRail.reset()
    cache.clear()
    yield
    _enabled_set.cache_clear()
    reset_rail_cache()
    cache.clear()


def fund(user, amount_minor, key=None):
    p = wallet.initiate_funding(user=user, amount_minor=amount_minor, network=Network.MTN, payer=user.phone,
                                idempotency_key=key or f"fund-{user.id}-{amount_minor}")
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    return p


def make_customer(phone, name, pin=PIN, amount=0):
    u = User.objects.create_user(phone=phone, full_name=name)
    u.set_pin(pin)
    u.save()
    if amount:
        fund(u, amount, key=f"seed-{phone}")
    return u


def client_for(user) -> APIClient:
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Bearer " + tokens.issue_tokens(user)["access"])
    return c


@pytest.fixture
def kofi(db):
    return make_customer("+233244000201", "Kofi Asante", amount=500_00)


@pytest.fixture
def ama(db):
    return make_customer("+233244000202", "Ama Serwaa", pin=OTHER_PIN, amount=300_00)


@pytest.fixture
def anon():
    return APIClient()
