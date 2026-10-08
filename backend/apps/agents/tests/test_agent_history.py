"""Agent transaction history API: filters, summary, cursor paging, masking, ownership."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents import services
from apps.agents.models import AgentTxn
from apps.licensing.gate import _enabled_set

User = get_user_model()
pytestmark = pytest.mark.django_db
URL = "/api/v1/agent/history"


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
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


def _agent(phone="+233200000090", name="Yaw's Kiosk"):
    user = User.objects.create_user(phone=phone, full_name="Agent")
    a = services.register_agent(user=user, display_name=name)
    services.activate_agent(a)
    return a


@pytest.fixture
def agent(db):
    return _agent()


def _client(agent):
    c = APIClient()
    c.force_authenticate(user=agent.user)
    return c


def test_history_summary_masking_and_float_effect(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=120_00)
    services.open_cash_out_window(User.objects.get(phone="+233244058519"))
    req = services.request_cash_out(agent=agent, customer_phone="+233244058519", amount_minor=20_00)
    services.approve_cash_out(customer=req.customer, request_id=req.id, pin="428173")
    body = _client(agent).get(URL, {"period": "today"}).json()

    kinds = [r["kind"] for r in body["results"]]
    assert kinds == ["cash_out", "cash_in", "topup"]                       # newest first
    cash_in = body["results"][1]
    assert cash_in["float_effect_display"] == "− GH₵ 120.00"
    assert body["results"][0]["float_effect_display"] == "+ GH₵ 20.00"
    assert cash_in["customer"] == "+23324•••8519" and cash_in["reference"].startswith("AG-")
    assert body["results"][2]["customer"] == ""                            # top-ups have none
    assert "+233244058519" not in str(body)

    s = body["summary"]
    assert s["cash_in"] == {"count": 1, "total_display": "GH₵ 120.00"}
    assert s["cash_out"] == {"count": 1, "total_display": "GH₵ 20.00"}
    assert s["topup"]["count"] == 1 and s["float_display"] == "GH₵ 400.00"

    # Home's recent list is masked too.
    recent = _client(agent).get("/api/v1/agent/transactions").json()
    assert all("•••" in r["customer_phone"] for r in recent if r["kind"] != "topup")


def test_filters(agent):
    services.topup_float(agent=agent, amount_minor=500_00)
    services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=10_00)
    old = services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=10_00)
    AgentTxn.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
    c = _client(agent)
    assert len(c.get(URL, {"kind": "cash_in"}).json()["results"]) == 2
    assert len(c.get(URL, {"kind": "cash_in", "period": "7d"}).json()["results"]) == 1
    assert c.get(URL, {"kind": "bogus"}).status_code == 400
    assert c.get(URL, {"period": "year"}).status_code == 400
    assert c.get(URL, {"cursor": "%%%"}).status_code == 400


def test_cursor_paging_complete_and_stable(agent):
    services.topup_float(agent=agent, amount_minor=1_000_00)
    for i in range(40):
        t = services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=1_00)
        AgentTxn.objects.filter(pk=t.pk).update(created_at=timezone.now() - timedelta(minutes=i + 1))
    c = _client(agent)
    seen, cursor, pages = [], None, 0
    while True:
        body = c.get(URL, {"cursor": cursor} if cursor else {}).json()
        assert (body["summary"] is None) == (cursor is not None)      # summary on page 1 only
        seen += [r["id"] for r in body["results"]]
        pages += 1
        if pages == 1:   # a new transaction mid-scroll must not shift later pages
            services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=1_00)
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 2 and len(seen) == 41 == len(set(seen))


def test_detail_only_for_own_transactions(agent):
    services.topup_float(agent=agent, amount_minor=100_00)
    t = services.cash_in(agent=agent, customer_phone="+233244058519", amount_minor=5_00)
    assert _client(agent).get(f"/api/v1/agent/transactions/{t.id}").json()["kind"] == "cash_in"
    other = _agent(phone="+233200000091", name="Other")
    assert _client(other).get(f"/api/v1/agent/transactions/{t.id}").status_code == 404
    assert _client(other).get(URL).json()["results"] == []


def test_non_agents_are_refused(db):
    c = APIClient()
    c.force_authenticate(user=User.objects.create_user(phone="+233200000092"))
    assert c.get(URL).status_code == 403
