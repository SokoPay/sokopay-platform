"""Configurable fees, maker-checker price changes, cash-out fee, agent commissions."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Sum
from django.utils import timezone

from apps.agents import services as agents
from apps.agents.models import Agent
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set
from apps.payments import fees
from apps.portal.tests.helpers import PASSWORD, login_verified
from apps.pricing import services as pricing
from apps.pricing.models import AgentCommission, PriceRule

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
def finance(db):
    users = []
    for i in (1, 2):
        u = User.objects.create_user(phone=f"+23320000005{i}", full_name=f"Fin {i}", password=PASSWORD,
                                     user_type="staff")
        u.groups.add(Group.objects.get(name="finance"))
        users.append(u)
    return users


def _approved(finance, **kw):
    r = pricing.propose(note="launch tariff", proposed_by=finance[0], **kw)
    return pricing.decide(r, approver=finance[1], approve=True)


def test_defaults_until_a_rule_is_approved_then_rule_applies(finance):
    assert fees.compute("bill", 100_00) == 50                 # launch default
    r = pricing.propose(kind="fee", product="bill", flat_minor=100, percent_bp=50, max_minor=2_00,
                        note="New bill tariff", proposed_by=finance[0])
    assert fees.compute("bill", 100_00) == 50                 # pending: not in force
    with pytest.raises(pricing.PricingError, match="different person"):
        pricing.decide(r, approver=finance[0], approve=True)
    pricing.decide(r, approver=finance[1], approve=True)
    assert fees.compute("bill", 100_00) == 1_00 + 50          # 1.00 + 0.5% of 100
    assert fees.compute("bill", 1000_00) == 2_00              # capped
    assert fees.compute("bill", 1_00) == 1_00                 # never more than the amount
    with pytest.raises(pricing.PricingError, match="already been decided"):
        pricing.decide(r, approver=finance[1], approve=False)


def test_future_rule_waits_and_backdating_is_refused(finance):
    later = timezone.now() + timedelta(days=2)
    _approved(finance, kind="fee", product="airtime", flat_minor=10, effective_from=later)
    assert fees.compute("airtime", 10_00) == 0
    assert pricing.fee("airtime", 10_00, at=later + timedelta(minutes=1)) == 10
    with pytest.raises(pricing.PricingError, match="past"):
        pricing.propose(kind="fee", product="data", flat_minor=10, note="oops backdated",
                        effective_from=timezone.now() - timedelta(days=1), proposed_by=finance[0])
    # Approved late: start moves to "now", never retroactive.
    r = pricing.propose(kind="fee", product="data", flat_minor=10, note="data fee",
                        effective_from=timezone.now() + timedelta(seconds=1), proposed_by=finance[0])
    PriceRule.objects.filter(pk=r.pk).update(effective_from=timezone.now() - timedelta(hours=3))
    r = pricing.decide(r, approver=finance[1], approve=True)
    assert r.effective_from >= timezone.now() - timedelta(minutes=1)


def test_typo_guards(finance):
    for kw in ({"percent_bp": 5000}, {"flat_minor": 500_00}, {"min_minor": 5_00, "max_minor": 1_00},
               {"flat_minor": -1}):
        with pytest.raises(pricing.PricingError):
            pricing.propose(kind="fee", product="bill", note="typo test", proposed_by=finance[0], **kw)
    with pytest.raises(pricing.PricingError):
        pricing.propose(kind="commission", product="bill", note="not priced", proposed_by=finance[0])


def test_bulk_fee_uses_rules(finance):
    from apps.bulk.fees import bulk_fee
    assert bulk_fee("momo", 100_00) == 75 and bulk_fee("sokopay", 100_00) == 0
    _approved(finance, kind="fee", product="bulk_bank", flat_minor=2_00)
    assert bulk_fee("bank", 100_00) == 2_00 and bulk_fee("momo", 100_00) == 75


@pytest.fixture
def agent_world(db):
    au = User.objects.create_user(phone="+233200000060", full_name="Esi Agent")
    agent = Agent.objects.create(user=au, display_name="Esi Kiosk", status="active")
    post_entry("float", [debit(accounts.partner_clearing("mock"), 1000_00),
                         credit(accounts.agent_float(str(agent.id)), 1000_00)])
    cust = User.objects.create_user(phone="+233244058519", full_name="Kofi")
    cust.set_pin(PIN)
    cust.save()
    return agent, cust


def test_cash_out_fee_and_commissions_accrue_then_pay(finance, agent_world):
    agent, cust = agent_world
    _approved(finance, kind="fee", product="cash_out", percent_bp=100, min_minor=50)     # 1%, min 0.50
    _approved(finance, kind="commission", product="cash_out", percent_bp=40)              # 0.4%
    _approved(finance, kind="commission", product="cash_in", percent_bp=20)               # 0.2%

    agents.cash_in(agent=agent, customer_phone=cust.phone, amount_minor=300_00)
    agents.open_cash_out_window(cust)
    req = agents.request_cash_out(agent=agent, customer_phone=cust.phone, amount_minor=200_00)
    assert req.fee_minor == 2_00
    req = agents.approve_cash_out(customer=cust, request_id=req.id, pin=PIN)
    assert req.status == "approved"
    wallet = natural_balance_of(accounts.customer_wallet(str(cust.id)))
    assert wallet == 300_00 - 200_00 - 2_00
    assert natural_balance_of(accounts.fee_revenue()) >= 2_00
    assert pricing.unpaid_minor(agent) == 60 + 80              # 0.2% of 300 + 0.4% of 200
    assert natural_balance_of(accounts.agent_commission(str(agent.id))) == 1_40

    float_before = agents.float_balance(agent)
    out = pricing.pay_commissions()
    assert out == {"agents_paid": 1, "total_minor": 1_40}
    assert agents.float_balance(agent) == float_before + 1_40 and pricing.unpaid_minor(agent) == 0
    assert pricing.pay_commissions()["agents_paid"] == 0          # nothing twice
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


def test_no_commission_on_tiny_or_own_transactions_and_fee_counts_for_balance(finance, agent_world):
    agent, cust = agent_world
    _approved(finance, kind="commission", product="cash_in", flat_minor=1_00)
    agents.cash_in(agent=agent, customer_phone=cust.phone, amount_minor=4_00)       # below minimum
    agents.cash_in(agent=agent, customer_phone=agent.user.phone, amount_minor=50_00)  # own wallet
    assert not AgentCommission.objects.exists()

    _approved(finance, kind="fee", product="cash_out", flat_minor=1_00)
    agents.open_cash_out_window(cust)
    req = agents.request_cash_out(agent=agent, customer_phone=cust.phone, amount_minor=4_00)
    req = agents.approve_cash_out(customer=cust, request_id=req.id, pin=PIN)
    assert req.status == "failed" and "fee" in req.failure_reason              # 4.00 + 1.00 > 4.00


def test_pricing_portal_maker_checker(finance):
    c1, c2 = login_verified(finance[0].phone), login_verified(finance[1].phone)
    assert c1.get("/dashboard/admin/pricing/").status_code == 200
    c1.post("/dashboard/admin/pricing/", {"action": "propose", "key": "fee:bill", "flat": "0.70",
                                          "percent": "", "note": "Raise bill fee"})
    r = PriceRule.objects.get()
    assert r.flat_minor == 70 and r.status == "pending"
    c1.post("/dashboard/admin/pricing/", {"action": "approve", "rule": r.pk})
    r.refresh_from_db()
    assert r.status == "pending"
    c2.post("/dashboard/admin/pricing/", {"action": "approve", "rule": r.pk})
    r.refresh_from_db()
    assert r.status == "approved" and fees.compute("bill", 10_00) == 70
    c1.post("/dashboard/admin/pricing/", {"action": "propose", "key": "fee:bill", "percent": "inf", "note": "bad"})
    assert PriceRule.objects.count() == 1
