"""
Agent app money features: selling airtime / bills from the float with a receipt, commission
per transaction, redeeming earnings into the float, the fortnightly cash-out payout (two
finance users), buying float by bank deposit (slip, staff approval) and float statements.
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents import services as agents
from apps.agents.models import AgentTxn, DepositAccount, FloatTopUp
from apps.common.models import AuditEvent
from apps.ledger.models import Posting
from apps.licensing.gate import _enabled_set
from apps.payments.models import Biller, Payment
from apps.portal.tests.helpers import PASSWORD, login_verified
from apps.pricing import services as pricing
from apps.pricing.models import AgentCommission, CommissionPayout
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import RailStatus

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
       b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture(autouse=True)
def _env(settings, tmp_path):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.MEDIA_ROOT = str(tmp_path)
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    reset_rail_cache()
    MockRail.reset()
    MockRail.set_bill_status(RailStatus.SUCCEEDED)
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()
    _enabled_set.cache_clear()


@pytest.fixture
def finance(db):
    users = []
    for i in (1, 2):
        u = User.objects.create_user(phone=f"+23320000007{i}", full_name=f"Fin {i}", password=PASSWORD,
                                     user_type="staff")
        u.groups.add(Group.objects.get(name="finance"))
        users.append(u)
    return users


@pytest.fixture
def agent(db):
    user = User.objects.create_user(phone="+233200000090", full_name="Yaw Agent")
    user.set_pin(PIN)
    user.save()
    a = agents.register_agent(user=user, display_name="Yaw's Kiosk")
    agents.activate_agent(a)
    agents.topup_float(agent=a, amount_minor=1000_00)
    return a


@pytest.fixture
def billers(db):
    return (Biller.objects.create(code="MTN_AIRTIME", name="MTN Airtime", category="airtime",
                                  rail_biller_code="mtn_airtime"),
            Biller.objects.create(code="ECG_PREPAID", name="ECG Prepaid", category="electricity",
                                  rail_biller_code="ecg_prepaid"))


def _client(agent):
    c = APIClient()
    c.force_authenticate(user=agent.user)
    return c


def _commissions(finance):
    for product, bp in (("airtime", 200), ("bill", 100), ("cash_in", 50), ("cash_out", 100)):
        r = pricing.propose(kind="commission", product=product, percent_bp=bp, note="agent rates",
                            proposed_by=finance[0])
        pricing.decide(r, approver=finance[1], approve=True)


def _sell_bill(agent, key="s1", **extra):
    body = {"biller_code": "ECG_PREPAID", "account": "P123456", "amount": "100.00", "pin": PIN,
            "receipt_phone": "0244058519", "receipt_email": "ama@example.com", **extra}
    return _client(agent).post("/api/v1/agent/sell/bill", body, format="json", HTTP_IDEMPOTENCY_KEY=key)


# --- selling -----------------------------------------------------------------------------------
def test_bill_sale_spends_float_earns_commission_and_sends_receipt(agent, billers, finance,
                                                                  django_capture_on_commit_callbacks):
    _commissions(finance)
    before = agents.float_balance(agent)
    with django_capture_on_commit_callbacks(execute=True):
        r = _sell_bill(agent)
    assert r.status_code == 201, r.content
    p = Payment.objects.get()
    assert p.status == "succeeded" and p.funding_source == "agent_float" and p.network == "agent"
    assert agents.float_balance(agent) == before - p.total_minor
    txn = AgentTxn.objects.get(payment=p)
    assert txn.kind == "bill" and txn.receipt_phone == "+233244058519"
    assert txn.commission.amount_minor == 1_00                     # 1% of GH₵100
    assert r.json()["transaction"]["commission_display"] == "GH₵ 1.00"
    assert len(mail.outbox) == 1 and p.reference in mail.outbox[0].body
    # The same request again (same Idempotency-Key): no second payment, no second commission.
    with django_capture_on_commit_callbacks(execute=True):
        _sell_bill(agent)
    assert Payment.objects.count() == 1 and AgentCommission.objects.count() == 1
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


def test_airtime_sale_shows_in_history_with_float_effect(agent, billers):
    r = _client(agent).post("/api/v1/agent/sell/airtime", {
        "biller_code": "MTN_AIRTIME", "account": "0244058519", "amount": "10", "pin": PIN,
        "receipt_phone": "0244058519"}, format="json", HTTP_IDEMPOTENCY_KEY="a1")
    assert r.status_code == 201, r.content
    rows = _client(agent).get("/api/v1/agent/history", {"kind": "airtime"}).json()["results"]
    assert len(rows) == 1 and rows[0]["float_effect_display"].startswith("−")
    assert rows[0]["reference"].startswith("AG-") and rows[0]["payment_reference"].startswith("SP-")


def test_sale_needs_pin_receipt_contact_and_enough_float(agent, billers):
    assert _sell_bill(agent, pin="000000").status_code in (400, 403)
    r = _sell_bill(agent, key="s2", receipt_phone="", receipt_email="")
    assert r.status_code == 400 and "receipt" in r.json()["error"]
    r = _sell_bill(agent, key="s3", amount="5000.00")
    assert r.status_code == 400 and "float" in str(r.json()).lower()
    assert agents.float_balance(agent) == 1000_00
    assert _client(agent).post("/api/v1/agent/sell/shoes", {"pin": PIN}, format="json").status_code == 404


def test_failed_biller_refunds_the_float(agent, billers):
    MockRail.set_bill_status(RailStatus.FAILED)
    r = _sell_bill(agent)
    assert r.status_code == 400
    assert agents.float_balance(agent) == 1000_00
    assert not AgentTxn.objects.filter(kind="bill").exists() and not AgentCommission.objects.exists()


def test_non_agent_cannot_sell(billers, db):
    u = User.objects.create_user(phone="+233244000111")
    c = APIClient()
    c.force_authenticate(user=u)
    assert c.post("/api/v1/agent/sell/bill", {"pin": PIN}, format="json").status_code == 403


# --- earnings ----------------------------------------------------------------------------------
def test_redeem_moves_sales_and_cash_in_commission_but_not_cash_out(agent, billers, finance):
    _commissions(finance)
    cust = User.objects.create_user(phone="+233244058519", full_name="Ama")
    cust.set_pin(PIN)
    cust.save()
    _sell_bill(agent)                                             # 1.00 redeemable
    agents.cash_in(agent=agent, customer_phone=cust.phone, amount_minor=200_00)   # 1.00 redeemable
    agents.open_cash_out_window(cust)
    req = agents.request_cash_out(agent=agent, customer_phone=cust.phone, amount_minor=100_00)
    agents.approve_cash_out(customer=cust, request_id=req.id, pin=PIN)           # 1.00 awaiting payout

    body = _client(agent).get("/api/v1/agent/earnings").json()
    assert body["redeemable"] == "GH₵ 2.00" and body["awaiting_payout"] == "GH₵ 1.00"
    assert {r["status"] for r in body["results"]} == {"redeemable", "awaiting_payout"}

    float_before = agents.float_balance(agent)
    assert _client(agent).post("/api/v1/agent/earnings/redeem", {"pin": "000000"}, format="json").status_code in (400, 403)
    r = _client(agent).post("/api/v1/agent/earnings/redeem", {"pin": PIN}, format="json")
    assert r.status_code == 200 and r.json()["redeemed"] == "GH₵ 2.00"
    assert agents.float_balance(agent) == float_before + 2_00
    r = _client(agent).post("/api/v1/agent/earnings/redeem", {"pin": PIN}, format="json")
    assert r.status_code == 400 and "nothing" in r.json()["error"].lower()
    assert AuditEvent.objects.filter(action="commission.redeem").count() == 1
    assert _client(agent).get("/api/v1/agent/me").json()["commission_display"] == "GH₵ 1.00"


def test_fortnightly_payout_needs_two_finance_users_and_holds_suspended_agents(agent, finance):
    other_user = User.objects.create_user(phone="+233200000091", full_name="Esi")
    other = agents.register_agent(user=other_user, display_name="Esi Kiosk")
    agents.activate_agent(other)
    for a, minor in ((agent, 3_00), (other, 5_00)):
        txn = AgentTxn.objects.create(agent=a, kind="cash_out", amount_minor=100_00)
        AgentCommission.objects.create(agent=a, txn=txn, product="cash_out", amount_minor=minor)
    from apps.ledger import accounts
    from apps.ledger.services import credit, debit, post_entry
    post_entry("accrue", [debit(accounts.commission_expense(), 8_00),
                          credit(accounts.agent_commission(str(agent.id)), 3_00),
                          credit(accounts.agent_commission(str(other.id)), 5_00)])
    agents.suspend_agent(other, actor=finance[0], reason="investigation")

    p = pricing.prepare_payout(prepared_by=finance[0])
    assert p.total_minor == 8_00 and p.agent_count == 2
    with pytest.raises(pricing.PricingError, match="already waiting"):
        pricing.prepare_payout(prepared_by=finance[1])
    with pytest.raises(pricing.PricingError, match="different"):
        pricing.decide_payout(p, approver=finance[0], approve=True)
    before = agents.float_balance(agent)
    p = pricing.decide_payout(p, approver=finance[1], approve=True)
    assert p.status == "paid" and p.total_minor == 3_00
    assert agents.float_balance(agent) == before + 3_00
    held = AgentCommission.objects.get(agent=other)
    assert held.paid_at is None and held.payout_id is None          # waits for the next payout
    assert pricing.earnings(agent)["next_payout_due"].date() == (timezone.now() + dt.timedelta(days=14)).date()


def test_payouts_page(agent, finance):
    txn = AgentTxn.objects.create(agent=agent, kind="cash_out", amount_minor=100_00)
    AgentCommission.objects.create(agent=agent, txn=txn, product="cash_out", amount_minor=1_00)
    client = login_verified(finance[0].phone)
    assert "Yaw" in client.get("/dashboard/admin/commission-payouts/").content.decode()
    client.post("/dashboard/admin/commission-payouts/", {"action": "prepare"})
    p = CommissionPayout.objects.get()
    client.post("/dashboard/admin/commission-payouts/", {"payout": p.pk, "decision": "approve"})
    p.refresh_from_db()
    assert p.status == "pending"                                    # the preparer can't approve
    client = login_verified(finance[1].phone)
    client.post("/dashboard/admin/commission-payouts/", {"payout": p.pk, "decision": "reject"})
    p.refresh_from_db()
    assert p.status == "rejected" and AgentCommission.objects.get().payout_id is None


# --- buying float by bank deposit --------------------------------------------------------------
@pytest.fixture
def bank(db):
    return DepositAccount.objects.create(bank_name="GCB Bank", account_name="Akaditi Limited",
                                         account_number="1234567890", branch="Ring Road")


def test_agent_deposit_with_slip_then_staff_approve(agent, bank, finance,
                                                    django_capture_on_commit_callbacks):
    accounts = _client(agent).get("/api/v1/agent/deposit-accounts").json()
    assert accounts == [{"id": str(bank.id), "bank_name": "GCB Bank", "account_name": "Akaditi Limited",
                         "account_number": "1234567890", "branch": "Ring Road"}]
    today = timezone.localdate().isoformat()
    r = _client(agent).post("/api/v1/agent/deposits", {
        "deposit_account": bank.id, "amount": "500", "reference": "GCB-77812", "deposit_date": today,
        "slip": SimpleUploadedFile("slip.png", PNG, content_type="image/png")}, format="multipart")
    assert r.status_code == 201, r.content
    assert r.json()["status"] == "pending" and r.json()["has_slip"]
    again = _client(agent).post("/api/v1/agent/deposits", {
        "deposit_account": bank.id, "amount": "500", "reference": "GCB-77812", "deposit_date": today},
        format="multipart")
    assert again.status_code == 400 and "already" in again.json()["error"]

    t = FloatTopUp.objects.get()
    assert t.channel == "agent" and t.slip_key
    staff = User.objects.create_user(phone="+233200000079", full_name="Ops", password=PASSWORD, user_type="staff")
    staff.groups.add(Group.objects.get(name="operations"))
    client = login_verified(staff.phone)
    page = client.get("/dashboard/admin/float-topups/").content.decode()
    assert "GCB-77812" in page and "GCB Bank" in page
    slip = client.get(f"/dashboard/admin/float-topups/{t.pk}/slip/")
    assert slip.status_code == 200 and slip.content == PNG and slip["Cache-Control"] == "no-store"
    assert AuditEvent.objects.filter(action="float.slip_view").exists()

    before = agents.float_balance(agent)
    with django_capture_on_commit_callbacks(execute=True):
        client.post("/dashboard/admin/float-topups/", {"topup": t.pk, "decision": "approve"})
    assert agents.float_balance(agent) == before + 500_00
    assert _client(agent).get("/api/v1/agent/deposits").json()[0]["status"] == "approved"


def test_deposit_validation(agent, bank):
    c = _client(agent)
    old = (timezone.localdate() - dt.timedelta(days=40)).isoformat()
    r = c.post("/api/v1/agent/deposits", {"deposit_account": bank.id, "amount": "500", "reference": "X-1234",
                                          "deposit_date": old}, format="json")
    assert r.status_code == 400 and "date" in r.json()["error"]
    bank.is_active = False
    bank.save()
    r = c.post("/api/v1/agent/deposits", {"deposit_account": bank.id, "amount": "500", "reference": "X-1234",
                                          "deposit_date": timezone.localdate().isoformat()}, format="json")
    assert r.status_code == 400 and "bank account" in r.json()["error"]
    assert c.get("/api/v1/agent/deposit-accounts").json() == []


def test_staff_manage_deposit_accounts(finance):
    staff = User.objects.create_user(phone="+233200000078", full_name="Ops", password=PASSWORD, user_type="staff")
    staff.groups.add(Group.objects.get(name="operations"))
    client = login_verified(staff.phone)
    client.post("/dashboard/admin/float-topups/", {"action": "add_account", "bank_name": "Ecobank",
                                                   "account_name": "Akaditi Limited", "account_number": "0011223344"})
    acc = DepositAccount.objects.get()
    client.post("/dashboard/admin/float-topups/", {"action": "toggle_account", "account": acc.pk})
    acc.refresh_from_db()
    assert not acc.is_active
    assert AuditEvent.objects.filter(action__startswith="float.deposit_account").count() == 2


# --- statements --------------------------------------------------------------------------------
def test_agent_float_statement(agent, billers):
    _sell_bill(agent)
    today = timezone.localdate().isoformat()
    r = _client(agent).get("/api/v1/agent/statements", {"from": today, "to": today, "file": "csv"})
    assert r.status_code == 200
    text = r.content.decode()
    assert "Yaw's Kiosk" in text or "Yaw" in text
    pdf = _client(agent).get("/api/v1/agent/statements", {"from": today, "to": today, "file": "pdf"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    u = User.objects.create_user(phone="+233244000222")
    c = APIClient()
    c.force_authenticate(user=u)
    assert c.get("/api/v1/agent/statements", {"from": today, "to": today}).status_code == 403
