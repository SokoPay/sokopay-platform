"""
Agent notifications (Agent app only) and their deep-link contract:
  {"type": "agent_status"} and {"type": "float"} → the app's home screen.
Also: float top-ups book against the configured rail's clearing account.
"""

import pytest
from django.contrib.auth import get_user_model

from apps.agents import services
from apps.ledger import accounts
from apps.ledger.services import natural_balance_of
from apps.licensing.gate import _enabled_set
from apps.notifications.models import Notification

User = get_user_model()
pytestmark = pytest.mark.django_db
AGENT_TYPES = {"float", "agent_status"}


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.AGENT_LOW_FLOAT_MINOR = 200_00
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
    user = User.objects.create_user(phone="+233200000080", full_name="Agent Yaw")
    a = services.register_agent(user=user, display_name="Yaw's Kiosk")
    services.activate_agent(a)
    return a


def _agent_notes(agent):
    return Notification.objects.filter(user=agent.user).order_by("created_at")


def _check(note):
    assert note.target_app == "agent"
    assert note.data.get("type") in AGENT_TYPES
    assert all(isinstance(v, str) for v in note.data.values())


def test_activation_and_topup_notify_the_agent(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    notes = list(_agent_notes(agent))
    assert [n.title for n in notes] == ["You're now a SokoPay agent", "Float topped up"]
    for n in notes:
        _check(n)
    assert "GH₵ 500.00" in notes[1].body


def test_low_float_warning_fires_once_when_crossing_threshold(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=250_00)   # 500 → 250
    assert not _agent_notes(agent).filter(title="Float running low").exists()
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=100_00)   # 250 → 150
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=50_00)    # 150 → 100
    low = _agent_notes(agent).filter(title="Float running low")
    assert low.count() == 1 and "GH₵ 150.00" in low.first().body
    _check(low.first())
    # Topping up and crossing again warns again.
    services.topup_float(agent=agent, amount_minor=300_00)                               # 100 → 400
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=250_00)   # 400 → 150
    assert _agent_notes(agent).filter(title="Float running low").count() == 2


def test_cash_in_customer_notice_still_goes_to_customer_app(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=10_00)
    note = Notification.objects.get(title="Cash deposited")
    assert note.target_app == "customer" and note.user.phone == "+233244058519"


def test_topup_books_against_the_configured_rail(settings, agent):
    settings.RAIL_PROVIDER = "korba"            # booking only; no rail call is made
    services.topup_float(agent=agent, amount_minor=100_00)
    assert natural_balance_of(accounts.partner_clearing("korba")) == 100_00
    assert natural_balance_of(accounts.partner_clearing("mock")) == 0
