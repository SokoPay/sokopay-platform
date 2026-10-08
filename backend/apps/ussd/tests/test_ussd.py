"""USSD: menu, balance, send, cash-out approval from a basic phone, gateway auth."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.agents import services as agents
from apps.agents.models import Agent
from apps.ledger import accounts
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.USSD_SHARED_SECRET = "s3cret"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def kofi(db):
    u = User.objects.create_user(phone="+233244058519", full_name="Kofi Mensah")
    u.set_pin(PIN)
    u.save()
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 300_00),
                        credit(accounts.customer_wallet(str(u.id)), 300_00)])
    return u


class Session:
    """Drives the gateway like a network would: cumulative text joined by '*'."""

    def __init__(self, phone, sid="s1"):
        self.c, self.phone, self.sid, self.parts = Client(), phone, sid, []

    def send(self, entry=None):
        if entry is not None:
            self.parts.append(entry)
        r = self.c.post("/api/v1/ussd/callback?key=s3cret", {"sessionId": self.sid, "serviceCode": "*713#",
                                                             "phoneNumber": self.phone, "text": "*".join(self.parts)})
        assert r.status_code == 200
        return r.content.decode()


def test_menu_and_balance(kofi):
    s = Session(kofi.phone)
    assert s.send().startswith("CON SokoPay\n1 Check balance")
    assert s.send("1") == "CON Enter your PIN"
    assert s.send(PIN) == "END Your SokoPay balance is GHS 300.00."


def test_send_to_sokopay_user(kofi):
    ama = User.objects.create_user(phone="+233244058600", full_name="Ama Serwaa")
    s = Session(kofi.phone, "s2")
    s.send()
    s.send("2")
    assert "Send to Ama S." in s.send("0244058600")
    assert "Enter PIN to confirm" in s.send("50")
    out = s.send(PIN)
    assert out.startswith("END Sent GHS 50.00 to Ama S.")
    assert natural_balance_of(accounts.customer_wallet(str(ama.id))) == 50_00


def test_wrong_pin_and_unknown_caller(kofi):
    s = Session(kofi.phone, "s3")
    s.send()
    s.send("1")
    assert s.send("000000").startswith("END ")
    assert natural_balance_of(accounts.customer_wallet(str(kofi.id))) == 300_00
    stranger = Session("+233249999999", "s4")
    assert "open an account" in stranger.send()


def test_cash_out_from_a_basic_phone(kofi):
    au = User.objects.create_user(phone="+233200000060", full_name="Esi Agent")
    agent = Agent.objects.create(user=au, display_name="Esi Kiosk", status="active")
    s = Session(kofi.phone, "s5")
    s.send()
    assert "Cash out is open" in s.send("4")                     # step 0: open the window
    agents.request_cash_out(agent=agent, customer_phone=kofi.phone, amount_minor=100_00)
    s2 = Session(kofi.phone, "s6")
    s2.send()
    assert "Esi Kiosk wants to pay you GHS 100.00" in s2.send("4")
    assert s2.send(PIN).startswith("END Approved. Collect GHS 100.00")
    assert natural_balance_of(accounts.customer_wallet(str(kofi.id))) == 200_00


def test_gateway_needs_the_shared_secret(kofi):
    r = Client().post("/api/v1/ussd/callback?key=wrong", {"sessionId": "x", "phoneNumber": kofi.phone, "text": ""})
    assert r.status_code == 403
