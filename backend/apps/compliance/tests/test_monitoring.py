"""Transaction monitoring rules, fired from the ledger post-commit hook."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.compliance import monitoring
from apps.compliance.models import Alert, LargeTransactionReport
from apps.ledger import accounts
from apps.ledger.models import JournalEntry, Posting
from apps.ledger.services import credit, debit, post_entry
from apps.notifications.models import Device

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(phone, name="Ama Mensah", age_days=60):
    u = User.objects.create_user(phone=phone, full_name=name)
    User.objects.filter(pk=u.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    u.refresh_from_db()
    return u


def _wallet(u):
    return accounts.customer_wallet(str(u.id))


def _in(u, minor, ref="remittance"):
    e = post_entry("in", [debit(accounts.partner_clearing("mock"), minor), credit(_wallet(u), minor)],
                   reference=(ref, "x"))
    monitoring.evaluate_entry(e.id)
    return e


def _out(u, minor, ref="payment", narrative="out"):
    e = post_entry(narrative, [debit(_wallet(u), minor), credit(accounts.fee_revenue(), minor)],
                   reference=(ref, "x"))
    monitoring.evaluate_entry(e.id)
    return e


def _p2p(a, b, minor):
    e = post_entry("p2p", [debit(_wallet(a), minor), credit(_wallet(b), minor)], reference=("p2p", str(a.id)))
    monitoring.evaluate_entry(e.id)
    return e


def _rules(user=None, kind="customer"):
    qs = Alert.objects.filter(subject_kind=kind)
    if user is not None:
        qs = qs.filter(user=user)
    return set(qs.values_list("rule", flat=True))


def test_hook_runs_after_commit_and_records_large_transactions(django_capture_on_commit_callbacks):
    u = _user("+233244000001")
    with django_capture_on_commit_callbacks(execute=True):
        post_entry("big", [debit(accounts.partner_clearing("mock"), 60_000_00), credit(_wallet(u), 60_000_00)],
                   reference=("remittance", "r1"))
    r = LargeTransactionReport.objects.get(user=u)
    assert r.amount_minor == 60_000_00 and r.direction == "in"


def test_velocity_and_dedup():
    u = _user("+233244000002")
    _in(u, 500_00)
    for _ in range(10):
        _out(u, 1_00)
    a = Alert.objects.get(user=u, rule="VELOCITY_OUT")
    _out(u, 1_00)
    a.refresh_from_db()
    assert a.hits == 2 and Alert.objects.filter(user=u, rule="VELOCITY_OUT").count() == 1   # one case, not many


def test_structuring_just_under_threshold():
    u = _user("+233244000003")
    for _ in range(3):
        _in(u, 9_500_00)
    assert "STRUCTURING" in _rules(u)
    v = _user("+233244000004")
    for _ in range(3):
        _in(v, 3_000_00)                         # nowhere near the threshold
    assert "STRUCTURING" not in _rules(v)


def test_pass_through_mule_pattern():
    u = _user("+233244000005")
    _in(u, 3_000_00)
    _out(u, 2_900_00)
    assert "PASS_THROUGH" in _rules(u)
    assert Alert.objects.get(user=u, rule="PASS_THROUGH").severity == "high"


def test_spending_a_balance_built_up_over_time_is_not_pass_through():
    u = _user("+233244000006")
    _in(u, 20_000_00)
    Posting.objects.filter(account=_wallet(u)).update(created_at=timezone.now() - timedelta(days=3))
    _in(u, 3_000_00)
    _out(u, 2_900_00)
    assert "PASS_THROUGH" not in _rules(u)         # plenty left in the wallet


def test_many_senders_into_one_wallet():
    target = _user("+233244000007")
    for i in range(10):
        s = _user(f"+2332440001{i:02d}", name=f"Sender {i}")
        _in(s, 100_00)
        _p2p(s, target, 50_00)
    assert "MANY_SENDERS" in _rules(target)


def test_new_account_high_outflow():
    u = _user("+233244000008", age_days=2)
    _in(u, 6_000_00)
    _out(u, 5_500_00)
    assert "NEW_ACCOUNT_HIGH_OUT" in _rules(u)


def test_dormant_account_reactivation():
    u = _user("+233244000009", age_days=400)
    e = _in(u, 5_000_00)
    Posting.objects.filter(entry=e).update(created_at=timezone.now() - timedelta(days=200))
    JournalEntry.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(days=200))
    _out(u, 2_500_00)
    assert "DORMANT_REACTIVATION" in _rules(u)


def test_new_device_then_large_outflow():
    u = _user("+233244000010", age_days=90)
    _in(u, 3_000_00)
    Posting.objects.filter(account=_wallet(u)).update(created_at=timezone.now() - timedelta(days=5))
    Device.objects.create(user=u, platform="android", app="customer", token="t" * 40)
    _out(u, 2_500_00)
    assert "NEW_DEVICE_HIGH_OUT" in _rules(u)


def test_refunds_are_not_customer_activity():
    u = _user("+233244000011", age_days=2)
    _in(u, 6_000_00)
    e = post_entry("Transfer out SP-1 (refunded)", [debit(accounts.interop_in_flight(), 5_000_00),
                                                    credit(_wallet(u), 5_000_00)], reference=("transfer", "t"),
                   allow_negative={accounts.interop_in_flight().code})
    assert monitoring.evaluate_entry(e.id) == 0


def test_agent_cash_cycling_and_split_cashout():
    from apps.agents.models import Agent
    agent_user = _user("+233200000080", name="Agent Yaw")
    agent = Agent.objects.create(user=agent_user, display_name="Yaw's Kiosk", status="active")
    cust = _user("+233244000012")
    flt, wal = accounts.agent_float(str(agent.id)), _wallet(cust)
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 5_000_00), credit(flt, 5_000_00)])

    def cash(kind, minor):
        lines = ([debit(flt, minor), credit(wal, minor)] if kind == "cash_in"
                 else [debit(wal, minor), credit(flt, minor)])
        e = post_entry(kind, lines, reference=(kind, str(agent.id)))
        monitoring.evaluate_entry(e.id)

    cash("cash_in", 1_000_00)
    cash("cash_out", 300_00)
    assert "AGENT_CASH_CYCLING" in _rules(kind="agent")
    cash("cash_out", 300_00)
    cash("cash_out", 300_00)
    assert "AGENT_SPLIT_CASHOUT" in _rules(kind="agent")


def test_merchant_spike_daily():
    from apps.merchants import onboarding
    from apps.payments.models import Payment
    owner = _user("+233200000090")
    m = onboarding.create_merchant(owner=owner, legal_name="Spike Ltd", business_type="registered")
    yesterday = timezone.localdate() - timedelta(days=1)

    def pay(n, amount, days_ago):
        p = Payment.objects.create(reference=f"SP-SPK{n:06d}", purpose="merchant", status="succeeded",
                                   merchant=m, amount_minor=amount, total_minor=amount, network="mtn", rail="mock")
        Payment.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=days_ago))

    for i in range(10):
        pay(i, 100_00, 5 + i)                          # normal: ~GH₵33/day average
    pay(99, 15_000_00, 1)                              # yesterday: GH₵15,000
    assert monitoring.run_daily(yesterday)["merchant_spikes"] == 1
    assert "MERCHANT_SPIKE" in _rules(kind="merchant")
