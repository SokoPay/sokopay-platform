"""
The only data the AI can see: a fixed set of aggregate tools.

Design rules (enforced here, not by the prompt):
  * no tool takes an identifier as input, and none returns names, phones, ID numbers,
    wallet IDs, references, addresses, free text written by users, or merchant names;
  * every count is k-anonymised (fewer than 5 → "<5", and its value suppressed);
  * inputs are clamped (e.g. at most 90 days) so a tool can't be used to dump history;
  * amounts are given in Ghana cedis as strings, converted from integer pesewas.
"""

from __future__ import annotations

import datetime as dt
import re

from django.db.models import Count, Sum
from django.utils import timezone

from apps.common.money import Money

from .. import anomalies, metrics, transactions
from .redact import k_anon

_CODE = re.compile(r"^[a-z0-9_]{1,40}$")


def _cedis(minor) -> str:
    return Money(int(minor or 0), "GHS").format()


def _group(count, value=None) -> dict:
    n = k_anon(count)
    out = {"count": n}
    if value is not None:
        out["value"] = "suppressed" if n == "<5" else _cedis(value)
    return out


def _days(value, default: int, cap: int) -> int:
    try:
        return max(1, min(int(value), cap))
    except (TypeError, ValueError):
        return default


def platform_overview(_args: dict) -> dict:
    u = metrics.users()
    s = transactions.day_summary(timezone.localdate())
    return {
        "as_of": timezone.now().isoformat(timespec="minutes"),
        "users": {"customers": k_anon(u["customers"]), "merchant_users": k_anon(u["merchant_users"]),
                  "agents": k_anon(u["agents"]), "new_today": k_anon(u["new_today"]),
                  "kyc_tiers": {f"tier_{k}": k_anon(v) for k, v in u["tiers"].items()},
                  "wallets_on_hold": k_anon(u["held"])},
        "today_by_status": {k: _group(v["count"], v["value"]) for k, v in s["by_status"].items()},
        "today_by_type": {v["label"]: _group(v["count"], v["completed_value"]) for v in s["by_type"].values()},
        "today_failure_rate_percent": s["failure_rate"],
    }


def daily_volumes(args: dict) -> dict:
    days = _days(args.get("days"), 14, 90)
    return {"days": [{"date": p["day"].isoformat(), "completed": _group(p["count"], p["value"]),
                      "failed": k_anon(p["failed"]), "failure_rate_percent": p["failure_rate"]}
                     for p in transactions.daily_series(days)]}


def failure_breakdown(args: dict) -> dict:
    """Failed transactions by type and failure code (codes are partner/system codes)."""
    from apps.payments.models import Payment
    from apps.wallet.models import ExternalTransfer
    days = _days(args.get("days"), 7, 30)
    since = timezone.now() - dt.timedelta(days=days)
    rows = []
    for label, qs in (("payment", Payment.objects.filter(mode="live", status="failed", created_at__gte=since)),
                      ("transfer", ExternalTransfer.objects.filter(status="failed", created_at__gte=since))):
        for code, n in qs.values_list("failure_code").annotate(n=Count("id")).order_by("-n")[:15]:
            code = (code or "unknown").lower()
            rows.append({"type": label, "code": code if _CODE.match(code) else "other", "count": k_anon(n)})
    return {"days": days, "failures": rows}


def aml_summary(args: dict) -> dict:
    from apps.compliance.models import Alert
    days = _days(args.get("days"), 7, 90)
    since = timezone.now() - dt.timedelta(days=days)
    qs = Alert.objects.filter(created_at__gte=since)
    return {
        "days": days,
        "raised_by_rule": {r: k_anon(n) for r, n in qs.values_list("rule").annotate(n=Count("id"))},
        "raised_by_severity": {s: k_anon(n) for s, n in qs.values_list("severity").annotate(n=Count("id"))},
        "open_now_by_status": {s: k_anon(n) for s, n in Alert.objects.filter(status__in=metrics.OPEN_ALERT)
                               .values_list("status").annotate(n=Count("id"))},
    }


def compliance_status(_args: dict) -> dict:
    c = metrics.compliance()
    return {"overall": c["status"],
            **{k: k_anon(c[k]) for k in ("alerts_high", "alerts_medium", "alerts_low", "overdue_high", "overdue_any",
                                         "screening_pending", "str_drafts", "str_to_file", "id_docs_pending",
                                         "merchants_in_review", "high_risk_merchants")}}


def settlement_summary(args: dict) -> dict:
    from apps.merchants.models import Settlement
    days = _days(args.get("days"), 7, 30)
    since = timezone.now() - dt.timedelta(days=days)
    groups = Settlement.objects.filter(created_at__gte=since).values_list("status") \
        .annotate(n=Count("id"), v=Sum("amount_minor"))
    return {"days": days, "by_status": {s: _group(n, v) for s, n, v in groups}}


def statistical_insights(_args: dict) -> dict:
    """The in-house findings, minus anything row-level (velocity accounts are counted only)."""
    found = []
    for i in anomalies.all_insights():
        if i["key"] == "velocity":
            found.append({"severity": i["severity"], "title": "Some accounts are sending money unusually fast",
                          "detail": f"{k_anon(len(i.get('accounts', [])))} account(s); staff can review them in the portal."})
        else:
            found.append({"severity": i["severity"], "title": i["title"], "detail": i["detail"]})
    return {"insights": found}


_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


def _days_schema(cap: int) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": cap,
                                    "description": f"How many days back (1-{cap})."}}}


TOOLS = {
    "platform_overview": (platform_overview, "Users by type and KYC tier, and today's transactions by status and type.", _NO_ARGS),
    "daily_volumes": (daily_volumes, "Completed transactions per day (count, value) and failure rate.", _days_schema(90)),
    "failure_breakdown": (failure_breakdown, "Failed payments and transfers by failure code.", _days_schema(30)),
    "aml_summary": (aml_summary, "AML alerts raised by rule and severity, and open alerts by status.", _days_schema(90)),
    "compliance_status": (compliance_status, "Overall compliance status and the queues behind it.", _NO_ARGS),
    "settlement_summary": (settlement_summary, "Merchant settlements by status (count, value).", _days_schema(30)),
    "statistical_insights": (statistical_insights, "Anomalies and forecasts computed by SokoPay's own statistics.", _NO_ARGS),
}


def tool_specs() -> list[dict]:
    return [{"name": name, "description": desc, "input_schema": schema} for name, (_, desc, schema) in TOOLS.items()]


def run(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": "Unknown tool."}
    return TOOLS[name][0](args if isinstance(args, dict) else {})
