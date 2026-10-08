"""
Agent cash-in / cash-out and float, plus the licence gate: these DEMI activities are
blocked under PSP Standard and work under DEMI. Books always balance to zero.
"""

import pytest
from django.contrib.auth import get_user_model

from apps.agents import services
from apps.agents.exceptions import AgentError
from apps.agents.models import Agent
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import natural_balance_of
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"   # agent activities need DEMI
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture(autouse=True)
def customer(db):
    """Cash-in/out need an existing SokoPay customer (no more ghost accounts)."""
    u = User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")
    u.set_pin("428173")
    u.save()
    return u


@pytest.fixture
def agent(db):
    user = User.objects.create_user(phone="+233200000040", full_name="Agent Yaw")
    a = services.register_agent(user=user, display_name="Yaw's Kiosk", location="Madina")
    services.activate_agent(a)
    return a


def test_register_and_activate(db):
    user = User.objects.create_user(phone="+233200000041", full_name="New Agent")
    a = services.register_agent(user=user, display_name="Kiosk")
    assert a.status == Agent.Status.PENDING
    user.refresh_from_db()
    assert user.user_type == "agent"
    services.activate_agent(a)
    assert a.status == Agent.Status.ACTIVE


def test_cash_in_moves_float_to_customer_wallet(agent):
    services.topup_float(agent=agent, amount_minor=500_00)      # GH₵500 float
    assert services.float_balance(agent) == 500_00

    txn = services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=120_00)
    assert txn.kind == "cash_in"
    assert services.float_balance(agent) == 380_00              # float down GH₵120

    customer = User.objects.get(phone="+233244058519")
    assert natural_balance_of(accounts.customer_wallet(str(customer.id))) == 120_00
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_cannot_cash_in_more_than_float(agent):
    services.topup_float(agent=agent, amount_minor=50_00)
    with pytest.raises(AgentError):
        services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=100_00)


def test_cash_out_debits_customer_wallet(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=200_00)
    # Customer now withdraws GH₵80 as cash: agent requests, customer approves with PIN.
    services.open_cash_out_window(User.objects.get(phone="+233244058519"))
    req = services.request_cash_out(agent=agent, customer_phone="+233244058519", amount_minor=80_00)
    services.approve_cash_out(customer=req.customer, request_id=req.id, pin="428173")

    customer = User.objects.get(phone="+233244058519")
    assert natural_balance_of(accounts.customer_wallet(str(customer.id))) == 120_00
    assert services.float_balance(agent) == 500_00 - 200_00 + 80_00
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_cash_out_fails_at_approval_when_customer_has_insufficient_balance(agent, customer):
    services.topup_float(agent=agent, amount_minor=500_00)
    services.open_cash_out_window(customer)
    req = services.request_cash_out(agent=agent, customer_phone=customer.phone, amount_minor=10_00)
    req = services.approve_cash_out(customer=customer, request_id=req.id, pin="428173")
    assert req.status == "failed" and "enough" in req.failure_reason
    assert services.float_balance(agent) == 500_00


def test_unknown_customer_number_is_refused(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    with pytest.raises(AgentError, match="No SokoPay account"):
        services.cash_in(agent=agent, customer_phone="+233209990000", amount_minor=10_00)
    assert not User.objects.filter(phone="+233209990000").exists()    # no ghost account


def test_agent_activities_blocked_under_psp_standard(settings, agent):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_STANDARD"
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=100_00)
