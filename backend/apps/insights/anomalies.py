"""
In-house insights: statistical checks run on SokoPay's own server. Nothing leaves it.

  volume        a day's completed transactions far from the recent normal (z-score)
  pace          today so far running far ahead of / behind a normal day
  failures      failure rate well above its recent average
  type_shift    one transaction type suddenly several times its usual count
  signups       new sign-ups far above normal (bot / promotion / fraud ring)
  velocity      accounts sending money unusually often in the last hour (internal list)
  aml_trend     an AML rule firing much more than usual
  backlog       compliance work waiting too long
  forecast      expected completed value tomorrow (7-day average ± spread)

Each insight: {"key", "severity": info|warning|critical, "title", "detail", "url"}.
Row-level findings (velocity) carry masked identities and are for staff only; the AI
tools never receive them (see apps.insights.ai.tools).
"""

from __future__ import annotations

import datetime as dt
import statistics

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone

from apps.common.money import Money
from apps.common.pagination import mask_phone

from . import transactions

User = get_user_model()
Z_WARN = 3.0
VELOCITY_PER_HOUR = 10


def _ghs(minor) -> str:
    return Money(int(minor), "GHS").format()


def _z(value: float, history: list[float]) -> float | None:
    if len(history) < 7:
        return None
    mean = statistics.fmean(history)
    spread = max(statistics.pstdev(history), max(1.0, 0.1 * mean))   # floor: quiet days aren't anomalies
    return (value - mean) / spread


def volume(series: list[dict]) -> list[dict]:
    out = []
    if len(series) < 9:
        return out
    yesterday, history = series[-2], [p["count"] for p in series[:-2]]
    z = _z(yesterday["count"], history)
    if z is not None and abs(z) >= Z_WARN:
        mean = statistics.fmean(history)
        direction = "above" if z > 0 else "below"
        out.append({"key": "volume", "severity": "warning",
                    "title": f"Yesterday's transactions were unusually {'high' if z > 0 else 'low'}",
                    "detail": f"{yesterday['count']} completed vs a usual {mean:.0f} a day ({abs(z):.1f} spreads {direction} normal).",
                    "url": "portal:admin_transactions"})
    return out


def pace(series: list[dict], now: dt.datetime | None = None) -> list[dict]:
    now = now or timezone.localtime()
    elapsed = (now.hour * 60 + now.minute) / 1440
    if elapsed < 0.25 or len(series) < 9:
        return []
    today, history = series[-1], [p["count"] for p in series[:-1]]
    projected = today["count"] / elapsed
    z = _z(projected, history)
    if z is not None and abs(z) >= Z_WARN:
        return [{"key": "pace", "severity": "warning",
                 "title": f"Today is running {'well ahead of' if z > 0 else 'well behind'} a normal day",
                 "detail": f"{today['count']} completed so far, on course for about {projected:.0f} "
                           f"vs a usual {statistics.fmean(history):.0f}.",
                 "url": "portal:admin_transactions"}]
    return []


def failures(series: list[dict]) -> list[dict]:
    if len(series) < 8:
        return []
    today = series[-1]
    finished = today["count"] + today["failed"]
    history = [p["failure_rate"] for p in series[:-1] if (p["count"] + p["failed"]) >= 5]
    if finished < 20 or len(history) < 5:
        return []
    avg = statistics.fmean(history)
    if today["failure_rate"] >= max(2 * avg, avg + 10):
        return [{"key": "failures", "severity": "critical" if today["failure_rate"] >= 40 else "warning",
                 "title": "Failure rate is up",
                 "detail": f"{today['failure_rate']}% of today's finished transactions failed vs a usual {avg:.1f}%. "
                           "Check the payment partner and the failure codes.",
                 "url": "portal:admin_transactions"}]
    return []


def type_shift(series: list[dict]) -> list[dict]:
    out = []
    if len(series) < 9:
        return out
    yesterday = series[-2]["by_type"]
    for key, count in yesterday.items():
        history = [p["by_type"].get(key, 0) for p in series[:-2]]
        mean = statistics.fmean(history) if history else 0
        if count >= 10 and count >= 3 * max(mean, 1):
            out.append({"key": f"type_shift:{key}", "severity": "info",
                        "title": f"{transactions.TYPES.get(key, key)} jumped yesterday",
                        "detail": f"{count} vs a usual {mean:.0f} a day ({count / max(mean, 1):.1f}×).",
                        "url": "portal:admin_transactions"})
    return out


def signups(days: int = 14) -> list[dict]:
    from django.db.models.functions import TruncDate
    today = timezone.localdate()
    start, _ = transactions.day_bounds(today - dt.timedelta(days=days))
    per_day = dict(User.objects.exclude(user_type="staff").filter(created_at__gte=start)
                   .annotate(d=TruncDate("created_at")).values_list("d").annotate(n=Count("id")))
    history = [per_day.get(today - dt.timedelta(days=i), 0) for i in range(1, days + 1)]
    now_count = per_day.get(today, 0)
    z = _z(now_count, history)
    if now_count >= 20 and z is not None and z >= Z_WARN:
        return [{"key": "signups", "severity": "warning",
                 "title": "Unusual surge in new sign-ups",
                 "detail": f"{now_count} new accounts today vs a usual {statistics.fmean(history):.0f}. "
                           "Check for a promotion, a bot or a fraud ring opening accounts.",
                 "url": "portal:admin_users"}]
    return []


def velocity(now: dt.datetime | None = None) -> list[dict]:
    """Accounts initiating many outgoing transactions in the last hour (staff-only detail)."""
    from apps.payments.models import Payment
    from apps.wallet.models import ExternalTransfer
    now = now or timezone.now()
    since = now - dt.timedelta(hours=1)
    counts: dict = {}
    for uid, n in Payment.objects.filter(created_at__gte=since, mode="live", user__isnull=False) \
            .values_list("user").annotate(n=Count("id")):
        counts[uid] = counts.get(uid, 0) + n
    for uid, n in ExternalTransfer.objects.filter(created_at__gte=since).values_list("sender").annotate(n=Count("id")):
        counts[uid] = counts.get(uid, 0) + n
    fast = sorted(((uid, n) for uid, n in counts.items() if n >= VELOCITY_PER_HOUR), key=lambda x: -x[1])[:10]
    if not fast:
        return []
    people = {u.pk: u for u in User.objects.filter(pk__in=[uid for uid, _ in fast])}
    who = ", ".join(f"{mask_phone(people[uid].phone)} ({n})" for uid, n in fast if uid in people)
    return [{"key": "velocity", "severity": "warning",
             "title": f"{len(fast)} account(s) sending money unusually fast",
             "detail": f"{VELOCITY_PER_HOUR}+ outgoing transactions in the last hour: {who}. Review them in Users.",
             "url": "portal:admin_users", "accounts": [str(uid) for uid, _ in fast]}]


def aml_trend(days: int = 14) -> list[dict]:
    from apps.compliance.models import Alert
    now = timezone.now()
    recent = dict(Alert.objects.filter(created_at__gte=now - dt.timedelta(hours=24))
                  .values_list("rule").annotate(n=Count("id")))
    before = dict(Alert.objects.filter(created_at__gte=now - dt.timedelta(days=days + 1),
                                       created_at__lt=now - dt.timedelta(hours=24))
                  .values_list("rule").annotate(n=Count("id")))
    out = []
    for rule, n in recent.items():
        daily = before.get(rule, 0) / days
        if n >= 3 and n >= 3 * max(daily, 0.5):
            out.append({"key": f"aml:{rule}", "severity": "warning",
                        "title": f"AML rule {rule} is firing more than usual",
                        "detail": f"{n} alerts in 24 hours vs about {daily:.1f} a day before.",
                        "url": "portal:compliance"})
    return out


def backlog() -> list[dict]:
    from apps.kyc.models import IdentityDocument
    from apps.merchants.models import Merchant
    day_ago = timezone.now() - dt.timedelta(hours=24)
    out = []
    docs = IdentityDocument.objects.filter(status="pending", created_at__lt=day_ago).count()
    if docs:
        out.append({"key": "backlog:id_docs", "severity": "info",
                    "title": f"{docs} ID document(s) waiting over a day",
                    "detail": "Customers were told about one working day.", "url": "portal:ops_kyc"})
    merchants = Merchant.objects.filter(status="submitted", updated_at__lt=day_ago - dt.timedelta(days=1)).count()
    if merchants:
        out.append({"key": "backlog:merchants", "severity": "info",
                    "title": f"{merchants} merchant application(s) waiting over two days",
                    "detail": "Pick them up in Merchants.", "url": "portal:merchant_queue"})
    return out


def forecast(series: list[dict]) -> list[dict]:
    full_days = series[:-1][-7:]
    if len(full_days) < 7:
        return []
    values = [p["value"] for p in full_days]
    mean, spread = statistics.fmean(values), statistics.pstdev(values)
    return [{"key": "forecast", "severity": "info", "title": "Expected tomorrow",
             "detail": f"About {_ghs(mean)} in completed transactions (typical range "
                       f"{_ghs(max(mean - spread, 0))} – {_ghs(mean + spread)}), from the last 7 days.",
             "url": "portal:admin_transactions"}]


ORDER = {"critical": 0, "warning": 1, "info": 2}


def all_insights(series: list[dict] | None = None) -> list[dict]:
    series = series or transactions.daily_series(15)
    found = (failures(series) + volume(series) + pace(series) + type_shift(series) + signups()
             + velocity() + aml_trend() + backlog() + forecast(series))
    return sorted(found, key=lambda i: ORDER.get(i["severity"], 3))
