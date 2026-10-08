"""Shared fixtures for merchant tests."""

import pytest
from django.contrib.auth import get_user_model

from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding
from apps.merchants.models import Merchant, SettlementAccount
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import RailStatus

User = get_user_model()


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.RAIL_WEBHOOK_SECRET = "test-secret"
    settings.DEFAULT_CURRENCY = "GHS"
    # Merchant aggregation/settlement are PSP Medium activities, so these tests run
    # the deployment at PSP_MEDIUM to exercise them.
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def owner(db):
    return User.objects.create_user(phone="+233200000001", full_name="Owner")


@pytest.fixture
def staff(db):
    return User.objects.create_user(phone="+233200000002", full_name="Staff", user_type="staff")


@pytest.fixture
def staff2(db):
    return User.objects.create_user(phone="+233200000003", full_name="Staff Two", user_type="staff")


@pytest.fixture
def approved_merchant(db, owner) -> Merchant:
    m = onboarding.create_merchant(
        owner=owner, legal_name="Ama Stores Ltd", business_type="registered",
        trading_name="Ama Stores",
    )
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return m


@pytest.fixture
def settlement_account(db, approved_merchant) -> SettlementAccount:
    return SettlementAccount.objects.create(
        merchant=approved_merchant, kind="momo", provider="mtn",
        account_no="+233244058519", account_name="Ama Stores",
        name_check_status="matched", is_default=True,
    )
