"""Unified transaction monitoring, platform metrics and the in-house statistical insights."""

import datetime as dt
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from apps.compliance.models import Alert
from apps.insights import anomalies, metrics, transactions
from apps.ledger.models import JournalEntry
from apps.merchants import onboarding
from apps.merchants.models import Settlement, SettlementAccount
from apps.payments.models import Payment
from apps.wallet.models import ExternalTransfer

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def kofi(db):
    return User.objects.create_user(phone="+233244000201", full_name="Kofi Asante")


def _pay(n, status="succeeded", amount=100_00, user=None, mode="live", purpose="bill"):
    return Payment.objects.create(reference=f"SP-MON{n:06d}", purpose=purpose, status=status, user=user,
                                  amount_minor=amount, fee_minor=50, total_minor=amount + 50, network="mtn",
                                  rail="mock", mode=mode)


def test_status_vocabulary_is_normalised():
    assert transactions.normalise("succeeded") == "completed"
    assert transactions.normalise("awaiting_approval") == "pending"
    assert transactions.normalise("processing") == "processing"
    assert transactions.normalise("declined") == "cancelled"
    assert transactions.normalise("refunded") == "reversed"
    assert transactions.normalise("failed") == "failed"


def test_day_summary_and_rows_cover_every_source(kofi):
    _pay(1, user=kofi)
    _pay(2, status="failed", user=kofi)
    _pay(3, status="pending", user=kofi)
    _pay(4, mode="test")                                       # merchant API test mode: never counted
    ExternalTransfer.objects.create(reference="TR-1", sender=kofi, destination_type="momo", institution="mtn",
                                    account="0244111222", amount_minor=50_00, status="pending")
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner")
    m = onboarding.create_merchant(owner=owner, legal_name="Shop Ltd", business_type="registered")
    acct = SettlementAccount.objects.create(merchant=m, kind="momo", provider="mtn", account_no="+233244000301",
                                            account_name="Owner")
    Settlement.objects.create(merchant=m, destination=acct, amount_minor=300_00, status="paid",
                              completed_at=timezone.now(), requested_by=owner)
    s = transactions.day_summary(timezone.localdate())
    assert s["by_status"]["completed"] == {"count": 2, "value": 400_00}
    assert s["by_status"]["failed"]["count"] == 1
    assert s["by_status"]["pending"]["count"] == 2              # payment + transfer
    assert s["failure_rate"] == round(100 / 3, 1)
    assert set(s["by_type"]) >= {"payment", "transfer", "settlement"}

    rows = transactions.rows(day=timezone.localdate())
    assert len(rows) == 5 and all(r["reference"] != "SP-MON000004" for r in rows)
    bill = next(r for r in rows if r["reference"] == "SP-MON000001")
    assert bill["type"] == "Payment: Bill payment" and bill["status"] == "completed" and bill["fee_minor"] == 50
    assert "0201" in bill["party"] and "+233244000201" not in bill["party"]       # masked
    assert [r["status"] for r in transactions.rows(day=timezone.localdate(), status="failed")] == ["failed"]
    assert {r["source"] for r in transactions.rows(day=timezone.localdate(), type_key="transfer")} == {"transfer"}
    assert len(transactions.rows(day=timezone.localdate(), search="sp-mon000002")) == 1


def test_transactions_cited_by_open_aml_alerts_are_flagged(kofi):
    p = _pay(1, user=kofi)
    _pay(2, user=kofi)
    entry = JournalEntry.objects.create(narrative="x", reference_type="payment", reference_id=str(p.pk))
    Alert.objects.create(rule="LARGE_TRANSACTION", title="t", severity="high", subject_kind="customer",
                         subject_id=str(kofi.pk), subject_label="k", user=kofi, evidence=[{"entry": str(entry.pk)}])
    flagged = transactions.rows(day=timezone.localdate(), flagged_only=True)
    assert [r["reference"] for r in flagged] == ["SP-MON000001"] and flagged[0]["flagged"]


def test_daily_series_groups_by_day(kofi):
    p = _pay(1, user=kofi)
    Payment.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=2))
    _pay(2, user=kofi)
    _pay(3, status="failed", user=kofi)
    series = transactions.daily_series(5)
    assert len(series) == 5
    assert series[-1]["count"] == 1 and series[-1]["failed"] == 1 and series[-1]["failure_rate"] == 50.0
    assert series[-3]["count"] == 1 and series[-3]["by_type"] == {"payment": 1}


def test_compliance_status_turns_red_for_old_high_alerts(kofi):
    assert metrics.compliance()["status"] == "green"
    a = Alert.objects.create(rule="X", title="t", severity="high", subject_kind="customer", subject_id="1",
                             subject_label="k", user=kofi)
    assert metrics.compliance()["status"] == "amber"
    Alert.objects.filter(pk=a.pk).update(created_at=timezone.now() - timedelta(hours=49))
    c = metrics.compliance()
    assert c["status"] == "red" and "over 48 hours" in c["reasons"][0]


def test_overview_counts_users_and_is_cached(kofi):
    o = metrics.overview()
    assert o["users"]["customers"] == 1 and o["users"]["total"] == 1
    User.objects.create_user(phone="+233244000202")
    assert metrics.overview()["users"]["total"] == 1            # cached for a few seconds
    cache.clear()
    assert metrics.overview()["users"]["total"] == 2


# --- in-house insights ----------------------------------------------------------------------
def _series(counts, failed=None, value=1000):
    today = timezone.localdate()
    out = []
    for i, n in enumerate(counts):
        f = (failed or [0] * len(counts))[i]
        finished = n + f
        out.append({"day": today - dt.timedelta(days=len(counts) - 1 - i), "count": n, "value": n * value,
                    "failed": f, "failure_rate": round(100 * f / finished, 1) if finished else 0.0,
                    "by_type": {"payment": n}})
    return out


def test_volume_spike_and_type_shift_are_reported():
    series = _series([100, 102, 98, 101, 99, 100, 103, 97, 100, 101, 99, 100, 400, 50])
    keys = [i["key"] for i in anomalies.volume(series) + anomalies.type_shift(series)]
    assert "volume" in keys and "type_shift:payment" in keys


def test_quiet_normal_days_produce_no_alarm():
    series = _series([100, 102, 98, 101, 99, 100, 103, 97, 100, 101, 99, 100, 102, 50])
    assert anomalies.volume(series) == [] and anomalies.type_shift(series) == []


def test_failure_rate_jump_is_critical():
    counts = [100] * 13 + [30]
    failed = [2] * 13 + [30]
    found = anomalies.failures(_series(counts, failed))
    assert found and found[0]["severity"] == "critical" and "50.0%" in found[0]["detail"]


def test_forecast_uses_last_seven_full_days():
    found = anomalies.forecast(_series([100] * 14))
    assert found and "GH₵ 1,000.00" in found[0]["detail"]


def test_velocity_lists_fast_senders_masked(kofi):
    for i in range(anomalies.VELOCITY_PER_HOUR):
        _pay(i, user=kofi)
    found = anomalies.velocity()
    assert found and "+23324•••0201" in found[0]["detail"] and str(kofi.pk) in found[0]["accounts"]
