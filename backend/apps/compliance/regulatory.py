"""
Monthly regulatory return (Bank of Ghana PSP / EMI reporting) — the numbers, from our
own records, for one calendar month.

[VERIFY] The Bank of Ghana prescribes its own return templates and submission portal
(and they differ by licence: PSP Standard / Medium / Enhanced, DEMI). This module
produces the underlying figures most returns ask for; the compliance team must map
them onto the current official template before filing. Nothing here is sent anywhere.

Sections:
  transactions   count + value by product (succeeded only)
  customers      registered, new, active, by KYC tier, on hold, closed
  e_money        month-end e-money liabilities (wallets, agent float, merchant balances)
                 and the latest safeguarding check
  agents         active / new / suspended, cash-in and cash-out volumes
  merchants      approved, new, refunds
  complaints     disputes opened and resolved, by outcome; average days to resolve
  aml            alerts by rule and severity, STRs filed, large transaction reports
"""

from __future__ import annotations

import csv
import datetime as dt
import io

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone


def month_bounds(year: int, month: int):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime(year, month, 1), tz)
    end = timezone.make_aware(dt.datetime(year + (month == 12), month % 12 + 1, 1), tz)
    return start, end


def _cv(qs, field="amount_minor") -> dict:
    agg = qs.aggregate(n=Count("id"), v=Sum(field))
    return {"count": agg["n"] or 0, "value_minor": int(agg["v"] or 0)}


def _liability_at(prefix: str, end) -> int:
    from apps.ledger.models import Posting
    raw = Posting.objects.filter(account__code__startswith=prefix, created_at__lt=end).aggregate(t=Sum("amount"))["t"]
    return -int(raw or 0)          # liabilities are credit-normal


def monthly_return(year: int, month: int) -> dict:
    from apps.agents.models import Agent, AgentTxn
    from apps.bulk.models import BulkPayoutItem
    from apps.kyc.models import KycProfile
    from apps.merchants.models import Dispute, Merchant, Refund, Settlement
    from apps.payments.models import Payment
    from apps.safeguarding.models import SafeguardingCheck
    from apps.wallet.models import ExternalTransfer, InboundRemittance

    from .models import Alert, LargeTransactionReport, SuspiciousTransactionReport

    start, end = month_bounds(year, month)
    in_month = Q(created_at__gte=start, created_at__lt=end)
    ok_pay = Payment.objects.filter(in_month, status=Payment.Status.SUCCEEDED, mode=Payment.Mode.LIVE)

    transactions = {
        **{f"payments_{purpose}": _cv(ok_pay.filter(purpose=purpose)) for purpose in Payment.Purpose.values},
        "merchant_payments_by_wallet": _cv(ok_pay.filter(purpose="merchant", funding_source="wallet")),
        "transfers_out_to_other_networks": _cv(ExternalTransfer.objects.filter(in_month, status="succeeded")),
        "inbound_remittances": _cv(InboundRemittance.objects.filter(in_month, status="credited")),
        "bulk_disbursements": _cv(BulkPayoutItem.objects.filter(in_month, status="paid")),
        "merchant_settlements": _cv(Settlement.objects.filter(in_month, status="paid")),
        "refunds": _cv(Refund.objects.filter(in_month, status="succeeded")),
        "agent_cash_in": _cv(AgentTxn.objects.filter(in_month, kind="cash_in")),
        "agent_cash_out": _cv(AgentTxn.objects.filter(in_month, kind="cash_out")),
        "fees_earned_minor": int(ok_pay.aggregate(f=Sum("fee_minor"))["f"] or 0),
    }

    User = get_user_model()
    customers_qs = User.objects.exclude(user_type="staff").filter(created_at__lt=end)
    active_ids = set(Payment.objects.filter(in_month, status="succeeded").exclude(user=None)
                     .values_list("user_id", flat=True))
    active_ids |= set(ExternalTransfer.objects.filter(in_month, status="succeeded").values_list("sender_id", flat=True))
    tiers = dict(KycProfile.objects.filter(user__created_at__lt=end).values_list("tier").annotate(n=Count("id")))
    customers = {
        "registered": customers_qs.count(),
        "new_in_month": customers_qs.filter(created_at__gte=start).count(),
        "active_in_month": len(active_ids),
        "by_kyc_tier": {label: tiers.get(value, 0) for value, label in KycProfile.Tier.choices},
        "wallets_on_hold": KycProfile.objects.filter(frozen=True).count(),
        "closed_in_month": User.objects.filter(closed_at__gte=start, closed_at__lt=end).count(),
    }

    latest_check = SafeguardingCheck.objects.filter(created_at__lt=end).order_by("-created_at").first()
    e_money = {
        "customer_wallets_minor": _liability_at("customer_wallet:", end),
        "agent_float_minor": _liability_at("agent_float:", end),
        "merchant_balances_minor": _liability_at("merchant_payable:", end),
        "safeguarding_status": latest_check.status if latest_check else "no_data",
        "safeguarding_trust_total_minor": latest_check.trust_total_minor if latest_check else None,
        "safeguarding_checked_at": latest_check.created_at.isoformat() if latest_check else None,
    }

    agents = {
        "active": Agent.objects.filter(status="active", created_at__lt=end).count(),
        "new_in_month": Agent.objects.filter(in_month).count(),
        "suspended": Agent.objects.filter(status="suspended").count(),
    }

    merchants = {
        "approved": Merchant.objects.filter(status=Merchant.Status.APPROVED, created_at__lt=end).count(),
        "new_in_month": Merchant.objects.filter(in_month).count(),
    }

    disputes_opened = Dispute.objects.filter(in_month)
    resolved = Dispute.objects.filter(decided_at__gte=start, decided_at__lt=end)
    days = [(d.decided_at - d.created_at).total_seconds() / 86400 for d in resolved.only("decided_at", "created_at")]
    complaints = {
        "disputes_opened": disputes_opened.count(),
        "disputes_opened_by_reason": dict(disputes_opened.values_list("reason").annotate(n=Count("id"))),
        "disputes_resolved": resolved.count(),
        "resolved_for_customer": resolved.filter(status="resolved_customer").count(),
        "resolved_for_merchant": resolved.filter(status="resolved_merchant").count(),
        "withdrawn": Dispute.objects.filter(status="withdrawn", updated_at__gte=start, updated_at__lt=end).count(),
        "average_days_to_resolve": round(sum(days) / len(days), 1) if days else None,
        "still_open_at_month_end": Dispute.objects.filter(created_at__lt=end, status__in=Dispute.ACTIVE).count(),
    }

    alerts = Alert.objects.filter(in_month)
    aml = {
        "alerts_raised": alerts.count(),
        "alerts_by_rule": dict(alerts.values_list("rule").annotate(n=Count("id"))),
        "alerts_by_severity": dict(alerts.values_list("severity").annotate(n=Count("id"))),
        "strs_filed": SuspiciousTransactionReport.objects.filter(status="filed", updated_at__gte=start,
                                                                updated_at__lt=end).count(),
        "large_transaction_reports": LargeTransactionReport.objects.filter(in_month).count(),
    }

    return {"period": f"{year:04d}-{month:02d}", "generated_at": timezone.now().isoformat(),
            "transactions": transactions, "customers": customers, "e_money": e_money, "agents": agents,
            "merchants": merchants, "complaints": complaints, "aml": aml}


def flatten(report: dict) -> list[tuple[str, str, object]]:
    """(section, metric, value) rows, nested dicts joined with '.'."""
    rows = []

    def walk(section, prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(section, f"{prefix}.{k}" if prefix else k, v)
        else:
            rows.append((section, prefix, value))
    for section in ("transactions", "customers", "e_money", "agents", "merchants", "complaints", "aml"):
        walk(section, "", report[section])
    return rows


def to_csv(report: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["SokoPay monthly regulatory figures", report["period"], "generated", report["generated_at"]])
    w.writerow(["Amounts ending _minor are in pesewas (GHS x 100). Map onto the current BoG template before filing."])
    w.writerow(["section", "metric", "value"])
    for row in flatten(report):
        w.writerow(row)
    return buf.getvalue().encode("utf-8")
