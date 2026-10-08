"""Back-office operations pages: role gating, agents, float maker-checker, KYC holds, recon, support."""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.agents import services as agents
from apps.agents.models import Agent, FloatTopUp
from apps.compliance import alerts
from apps.kyc.limits import profile_for
from apps.licensing.gate import _enabled_set
from apps.reconciliation.models import ReconItem, ReconRun

from .helpers import PASSWORD, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


def _staff(phone, name, *groups):
    u = User.objects.create_user(phone=phone, full_name=name, password=PASSWORD, user_type="staff")
    for g in groups:
        u.groups.add(Group.objects.get(name=g))
    return u


@pytest.fixture
def ops1(db):
    return _staff("+233200000201", "Ops One", "operations")


@pytest.fixture
def ops2(db):
    return _staff("+233200000202", "Ops Two", "operations")


@pytest.fixture
def customer(db):
    u = User.objects.create_user(phone="+233244058600", full_name="Esi Agent")
    u.set_pin("428173")
    u.save()
    return u


def test_pages_are_role_gated(ops1):
    support = _staff("+233200000203", "Support", "support")
    s = login_verified(support.phone)
    for url in ("/dashboard/admin/agents/", "/dashboard/admin/float-topups/", "/dashboard/admin/cash-outs/",
                "/dashboard/admin/kyc/", "/dashboard/admin/reconciliation/"):
        assert s.get(url).status_code == 403, url
    assert s.get("/dashboard/admin/support/").status_code == 200
    o = login_verified(ops1.phone)
    assert o.get("/dashboard/admin/agents/").status_code == 200
    assert o.get("/dashboard/admin/kyc/").status_code == 403          # compliance only
    assert o.get("/dashboard/admin/reconciliation/").status_code == 403  # finance only
    nav = o.get("/dashboard/admin/agents/").content
    assert b"Agents" in nav and b"Recon<" not in nav


def test_register_activate_topup_maker_checker_and_suspend(ops1, ops2, customer):
    c1 = login_verified(ops1.phone)
    r = c1.post("/dashboard/admin/agents/", {"phone": customer.phone, "display_name": "Esi Kiosk"})
    agent = Agent.objects.get(user=customer)
    assert r["Location"].endswith(f"/agents/{agent.pk}/") and agent.status == "pending"

    url = f"/dashboard/admin/agents/{agent.pk}/"
    c1.post(url, {"action": "activate"})
    agent.refresh_from_db()
    assert agent.status == "active"

    c1.post(url, {"action": "topup", "amount": "500", "payment_reference": "GCB-123"})
    t = FloatTopUp.objects.get()
    assert t.status == "pending" and agents.float_balance(agent) == 0
    # Same reference can't be used twice.
    c1.post(url, {"action": "topup", "amount": "500", "payment_reference": "GCB-123"})
    assert FloatTopUp.objects.count() == 1

    # The maker can't approve their own top-up.
    c1.post("/dashboard/admin/float-topups/", {"topup": t.pk, "decision": "approve"})
    t.refresh_from_db()
    assert t.status == "pending" and agents.float_balance(agent) == 0

    c2 = login_verified(ops2.phone)
    c2.post("/dashboard/admin/float-topups/", {"topup": t.pk, "decision": "approve", "note": "seen in GCB"})
    t.refresh_from_db()
    assert t.status == "approved" and t.decided_by == ops2 and agents.float_balance(agent) == 500_00
    # Deciding twice does nothing.
    c2.post("/dashboard/admin/float-topups/", {"topup": t.pk, "decision": "approve"})
    assert agents.float_balance(agent) == 500_00

    c1.post(url, {"action": "suspend", "reason": ""})
    agent.refresh_from_db()
    assert agent.status == "active"          # reason required
    c1.post(url, {"action": "suspend", "reason": "Customer complaints"})
    agent.refresh_from_db()
    assert agent.status == "suspended"
    assert c1.get("/dashboard/admin/cash-outs/").status_code == 200


def test_kyc_manual_hold_and_aml_hold_cannot_be_released_here(customer):
    officer = _staff("+233200000204", "Compliance", "compliance")
    c = login_verified(officer.phone)
    c.post("/dashboard/admin/kyc/", {"account": customer.phone, "reason": "Police letter", "action": "hold"})
    assert profile_for(customer).frozen
    page = c.get("/dashboard/admin/kyc/")
    assert b"Police letter" in page.content
    c.post("/dashboard/admin/kyc/", {"account": customer.phone, "reason": "Letter withdrawn", "action": "release"})
    assert not profile_for(customer).frozen

    alert = alerts.raise_alert(rule="PASS_THROUGH", title="x", severity="high", subject_kind="customer",
                               subject_id=str(customer.id), subject_label="Esi", user=customer, evidence={})
    alerts.place_hold(alert, actor=officer, reason="Mule pattern")
    assert profile_for(customer).frozen
    c.post("/dashboard/admin/kyc/", {"account": customer.phone, "reason": "nope", "action": "release"})
    assert profile_for(customer).frozen       # AML holds are released from the alert only


def test_recon_resolve_needs_note_and_records_who(db):
    fin = _staff("+233200000205", "Finance", "finance")
    run = ReconRun.objects.create(rail="mock", date=datetime.date(2026, 10, 1), mismatch_count=1)
    item = ReconItem.objects.create(run=run, kind=ReconItem.Kind.choices[0][0], rail_ref="X1",
                                    ours_minor=1000, theirs_minor=900)
    c = login_verified(fin.phone)
    assert b"X1" in c.get(f"/dashboard/admin/reconciliation/{run.pk}/").content
    c.post(f"/dashboard/admin/reconciliation/{run.pk}/", {"item": item.pk, "note": ""})
    item.refresh_from_db()
    assert not item.resolved
    c.post(f"/dashboard/admin/reconciliation/{run.pk}/", {"item": item.pk, "note": "Partner fee, booked"})
    item.refresh_from_db()
    assert item.resolved and item.resolved_by == fin and item.resolved_at


def test_support_lookup_shows_account_but_not_aml_reason(customer):
    support = _staff("+233200000206", "Support", "support")
    alert = alerts.raise_alert(rule="PASS_THROUGH", title="x", severity="high", subject_kind="customer",
                               subject_id=str(customer.id), subject_label="Esi", user=customer, evidence={})
    alerts.place_hold(alert, actor=support, reason="Secret mule finding")
    c = login_verified(support.phone)
    page = c.get("/dashboard/admin/support/", {"q": customer.phone})
    assert page.status_code == 200
    assert b"Esi Agent" in page.content and b"Wallet on hold" in page.content
    assert b"Secret mule finding" not in page.content and b"AML" not in page.content   # no tipping-off
    gen = customer.token_generation
    c.post(f"/dashboard/admin/support/?q={customer.phone}", {"action": "logout_all"})
    customer.refresh_from_db()
    assert customer.token_generation == gen + 1
    assert b"No SokoPay account" in c.get("/dashboard/admin/support/", {"q": "+233249999999"}).content \
        or b"not" in c.get("/dashboard/admin/support/", {"q": "+233249999999"}).content
