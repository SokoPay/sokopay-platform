"""
Cash-out needs the CUSTOMER's approval ("Allow CashOut"): an agent can only request;
money moves when the customer approves with their PIN in their own app.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents import services
from apps.agents.exceptions import AgentError
from apps.agents.models import CashOutRequest
from apps.ledger.models import Posting
from apps.licensing.gate import _enabled_set
from apps.notifications.models import Notification

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def agent(db):
    u = User.objects.create_user(phone="+233200000070", full_name="Agent Yaw")
    a = services.register_agent(user=u, display_name="Yaw's Kiosk", location="Madina")
    services.activate_agent(a)
    services.topup_float(agent=a, amount_minor=1_000_00)
    return a


@pytest.fixture
def ama(agent):
    u = User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")
    u.set_pin(PIN)
    u.save()
    services.cash_in(agent=agent, customer_phone=u.phone, amount_minor=300_00)
    return u


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def test_request_moves_nothing_until_customer_approves(agent, ama):
    a, c = _client(agent.user), _client(ama)
    assert c.post("/api/v1/wallet/cash-out/allow").status_code == 201        # customer presses Cash out
    assert c.get("/api/v1/wallet/cash-out-requests").json()["window"] is not None
    r = a.post("/api/v1/agent/cash-out", {"customer_phone": ama.phone, "amount": "50.00"}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "pending"
    assert r.json()["customer"] == "+23324•••8519"
    assert services.wallet_balance(ama) == 300_00                       # untouched
    note = Notification.objects.get(user=ama, title="Approve cash-out?")
    assert note.target_app == "customer" and note.data["type"] == "cashout_request"

    pending = c.get("/api/v1/wallet/cash-out-requests").json()
    assert pending["window"] is None                                       # used by this request
    pending = pending["requests"]
    assert len(pending) == 1 and pending[0]["agent_name"] == "Yaw's Kiosk"
    rid = pending[0]["id"]

    assert c.post(f"/api/v1/wallet/cash-out-requests/{rid}/approve", {"pin": "000001"},
                  format="json").status_code == 400                     # wrong PIN
    assert services.wallet_balance(ama) == 300_00
    ok = c.post(f"/api/v1/wallet/cash-out-requests/{rid}/approve", {"pin": PIN}, format="json")
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert services.wallet_balance(ama) == 250_00
    assert services.float_balance(agent) == 1_000_00 - 300_00 + 50_00
    assert a.get(f"/api/v1/agent/cash-out/{rid}").json()["status"] == "approved"
    assert Notification.objects.filter(user=agent.user, title="Cash-out approved").exists()
    # Approving twice does nothing more.
    assert c.post(f"/api/v1/wallet/cash-out-requests/{rid}/approve", {"pin": PIN}, format="json").status_code == 400
    assert services.wallet_balance(ama) == 250_00
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_decline_and_expiry(agent, ama):
    services.open_cash_out_window(ama)
    req = services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)
    services.decline_cash_out(customer=ama, request_id=req.id)
    req.refresh_from_db()
    assert req.status == "declined"
    assert Notification.objects.filter(user=agent.user, title="Cash-out declined").exists()

    services.open_cash_out_window(ama)
    req = services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)
    CashOutRequest.objects.filter(pk=req.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(AgentError, match="expired"):
        services.approve_cash_out(customer=ama, request_id=req.id, pin=PIN)
    assert services.wallet_balance(ama) == 300_00
    assert list(services.pending_cash_outs(ama)) == []


def test_one_open_request_per_customer_and_no_self_cash_out(agent, ama):
    services.open_cash_out_window(ama)
    services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)
    services.open_cash_out_window(ama)
    with pytest.raises(AgentError, match="already has a cash-out waiting"):
        services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=20_00)
    with pytest.raises(AgentError, match="own wallet"):
        services.request_cash_out(agent=agent, customer_phone=agent.user.phone, amount_minor=10_00)


def test_only_the_customer_can_approve(agent, ama):
    services.open_cash_out_window(ama)
    req = services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)
    mallory = User.objects.create_user(phone="+233200000071")
    mallory.set_pin(PIN)
    mallory.save()
    r = _client(mallory).post(f"/api/v1/wallet/cash-out-requests/{req.id}/approve", {"pin": PIN}, format="json")
    assert r.status_code == 400
    # The agent can't approve on the customer's behalf through any endpoint either.
    r = _client(agent.user).post(f"/api/v1/wallet/cash-out-requests/{req.id}/approve", {"pin": PIN},
                                 format="json")
    assert r.status_code == 400
    req.refresh_from_db()
    assert req.status == "pending" and services.wallet_balance(ama) == 300_00


def test_insufficient_balance_is_told_to_customer_not_agent(agent, ama):
    services.open_cash_out_window(ama)
    r = _client(agent.user).post("/api/v1/agent/cash-out",
                                 {"customer_phone": ama.phone, "amount": "900.00"}, format="json")
    assert r.status_code == 201                                       # agent learns nothing
    rid = r.json()["id"]
    resp = _client(ama).post(f"/api/v1/wallet/cash-out-requests/{rid}/approve", {"pin": PIN}, format="json")
    assert resp.status_code == 400 and "enough" in resp.json()["error"]
    status = _client(agent.user).get(f"/api/v1/agent/cash-out/{rid}").json()
    assert status["status"] == "failed" and "300.00" not in str(status) and "300_00" not in str(status)   # balance not leaked


def test_agent_cannot_request_unless_customer_pressed_cash_out(agent, ama):
    r = _client(agent.user).post("/api/v1/agent/cash-out",
                                 {"customer": ama.phone, "amount": "10.00"}, format="json")
    assert r.status_code == 400 and "press Cash out" in r.json()["error"]
    assert not CashOutRequest.objects.exists()
    assert not Notification.objects.filter(user=ama, title="Approve cash-out?").exists()


def test_window_allows_one_request_and_can_be_cancelled_or_expire(agent, ama):
    from apps.agents.models import CashOutWindow
    services.open_cash_out_window(ama)
    req = services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)
    services.decline_cash_out(customer=ama, request_id=req.id)
    with pytest.raises(AgentError, match="press Cash out"):          # window already used
        services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)

    c = _client(ama)
    c.post("/api/v1/wallet/cash-out/allow")
    assert c.delete("/api/v1/wallet/cash-out/allow").status_code == 204
    with pytest.raises(AgentError, match="press Cash out"):
        services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)

    services.open_cash_out_window(ama)
    CashOutWindow.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(AgentError, match="press Cash out"):
        services.request_cash_out(agent=agent, customer_phone=ama.phone, amount_minor=10_00)
