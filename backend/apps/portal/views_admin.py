"""
Back-office command centre: live overview, users, transactions, payments & settlement,
compliance & risk, and insights (in-house statistics plus the optional AI assistant).

Security model:
  * Role-based: each page names the staff roles that may open it (superusers: all).
  * Least data: lists show masked phone numbers; the full profile is one click away and
    every opening of it is written to the audit log ("user.view").
  * Every state change (flag, hold, disable, export, AI use) needs a reason where it
    matters and is audit-logged with the staff member's name; staff can't act on staff.
  * Live panels are cached aggregates, polled every 15 s (no per-user data in them).
  * Exports are limited (one day, 5,000 rows, rate-limited) and masked.
"""

from __future__ import annotations

import csv
import datetime as dt

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.common.audit import record as audit
from apps.common.money import Money
from apps.common.pagination import mask_phone
from apps.compliance import alerts as aml
from apps.compliance.models import Alert
from apps.insights import anomalies, metrics, transactions
from apps.insights.ai import gateway as ai
from apps.kyc.models import IdentityDocument, KycProfile

from . import ratelimit
from .decorators import has_staff_role, staff_required, staff_role_required

User = get_user_model()
USER_ROLES = ("support", "operations", "compliance")
TXN_ROLES = ("operations", "finance", "compliance", "support")
EXPORT_ROLES = ("finance", "compliance")
PAYMENT_ROLES = ("finance", "operations")
INSIGHT_ROLES = ("compliance", "finance", "operations")
EXPORT_MAX_ROWS = 5000


def _ghs(minor) -> str:
    return Money(int(minor or 0), "GHS").format()


def _spark(values: list[int], width: int = 280, height: int = 48) -> str:
    """SVG polyline points for a small trend chart (no JavaScript, no external library)."""
    if not values:
        return ""
    top = max(values) or 1
    step = width / max(len(values) - 1, 1)
    return " ".join(f"{i * step:.1f},{height - (v / top) * (height - 4) - 2:.1f}" for i, v in enumerate(values))


# --- overview ------------------------------------------------------------------------------------
def _overview_context():
    o = metrics.overview()
    today, yesterday = o["today"], o["yesterday"]
    prev = yesterday["completed_value"]
    change = round(100 * (today["completed_value"] - prev) / prev, 1) if prev else None
    return {"o": o, "ghs": _ghs, "change": change,
            "spark": _spark([p["value"] for p in o["series"]]),
            "spark_days": [p["day"] for p in o["series"]],
            "status_labels": transactions.STATUS_LABEL}


@staff_required
def dashboard(request):
    return render(request, "portal/admin/overview.html", _overview_context())


@staff_required
def dashboard_live(request):
    """The overview body only, re-fetched every 15 s by htmx."""
    return render(request, "portal/admin/_overview_live.html", _overview_context())


# --- users ---------------------------------------------------------------------------------------
def _open_alerts():
    return Alert.objects.filter(user=OuterRef("pk"), status__in=metrics.OPEN_ALERT)


@staff_role_required(*USER_ROLES)
def users(request):
    from apps.wallet.accounts_lookup import normalise_phone
    qs = (User.objects.exclude(user_type="staff")
          .select_related("kyc").annotate(flagged=Exists(_open_alerts())).order_by("-created_at"))
    q = (request.GET.get("q") or "").strip()
    if q:
        phone = normalise_phone(q)
        cond = Q(full_name__icontains=q) | Q(email__iexact=q)
        if phone:
            cond |= Q(phone=phone)
        if q.isdigit() and len(q) == 10:
            cond |= Q(wallet_number__number=q)
        qs = qs.filter(cond)
    kind = request.GET.get("type", "")
    if kind in ("consumer", "merchant", "agent"):
        qs = qs.filter(user_type=kind)
    state = request.GET.get("state", "")
    if state == "flagged":
        qs = qs.filter(flagged=True)
    elif state == "held":
        qs = qs.filter(kyc__frozen=True)
    elif state == "disabled":
        qs = qs.filter(is_active=False, closed_at__isnull=True)
    elif state == "closed":
        qs = qs.filter(closed_at__isnull=False)
    tier = request.GET.get("tier", "")
    if tier in ("0", "1", "2"):
        qs = qs.filter(kyc__tier=int(tier)) if tier != "0" else qs.filter(Q(kyc__isnull=True) | Q(kyc__tier=0))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    for u in page:
        u.phone_masked = mask_phone(u.phone)
    return render(request, "portal/admin/users.html",
                  {"page": page, "q": q, "type": kind, "state": state, "tier": tier,
                   "counts": metrics.users()})


def _subject_kind(user) -> str:
    if user.user_type == "agent":
        return "agent"
    if user.user_type == "merchant":
        return "merchant"
    return "customer"


@staff_role_required(*USER_ROLES)
def user_detail(request, pk):
    subject = get_object_or_404(User.objects.select_related("kyc"), pk=pk)
    if subject.user_type == "staff":
        raise PermissionDenied("Staff accounts are managed in the Django admin, not here.")
    if request.method == "POST":
        return _user_action(request, subject)
    audit("user.view", actor=request.user, obj=subject)        # every look at a full profile is recorded
    from apps.activity.services import feed
    from apps.ledger.models import LedgerAccount
    from apps.ledger.services import natural_balance_of
    from apps.merchants.models import MerchantMember
    acct = LedgerAccount.objects.filter(code=f"customer_wallet:{subject.pk}").first()
    try:
        activity = feed(subject, size=25)["results"]
    except Exception:  # an odd ledger row must not stop staff seeing the profile
        activity = []
    kyc = KycProfile.objects.filter(user=subject).first()
    return render(request, "portal/admin/user_detail.html", {
        "u": subject,
        "kyc": kyc,
        "balance": _ghs(natural_balance_of(acct)) if acct else _ghs(0),
        "documents": IdentityDocument.objects.filter(user=subject).exclude(status="replaced")[:10],
        "alerts": Alert.objects.filter(user=subject).order_by("-created_at")[:20],
        "memberships": MerchantMember.objects.filter(user=subject).select_related("merchant"),
        "agent": getattr(subject, "agent_profile", None),
        "activity": activity,
        "can_hold": has_staff_role(request.user, "compliance"),
        "can_disable": has_staff_role(request.user, "compliance", "operations"),
    })


def _user_action(request, subject):
    from apps.kyc import services as kyc
    action = request.POST.get("action", "")
    reason = (request.POST.get("reason") or "").strip()
    back = redirect("portal:admin_user", pk=subject.pk)
    if not reason:
        messages.error(request, "A reason is required. It's kept in the audit log.")
        return back
    if action == "flag":
        severity = request.POST.get("severity", "medium")
        if severity not in ("low", "medium", "high"):
            severity = "medium"
        alert = aml.raise_alert(
            rule="MANUAL_REVIEW", title=f"Flagged by staff for review: {reason[:100]}", severity=severity,
            subject_kind=_subject_kind(subject), subject_id=str(subject.pk),
            subject_label=f"{subject.full_name or 'Unnamed'} {mask_phone(subject.phone)}", user=subject,
            evidence={"reason": reason[:255], "at": timezone.now().isoformat()})
        aml.add_note(alert, author=request.user, text=f"Flagged for review: {reason}")
        audit("user.flag", actor=request.user, obj=subject, severity=severity, alert=str(alert.pk))
        messages.success(request, "Flagged for compliance review. It's in the AML queue.")
    elif action in ("hold", "release"):
        if not has_staff_role(request.user, "compliance"):
            raise PermissionDenied("Only compliance can place or release holds.")
        if action == "hold":
            kyc.freeze(subject, reason=f"Manual hold: {reason}", actor=request.user)
            messages.success(request, "Wallet on hold: money can't leave it.")
        else:
            profile = KycProfile.objects.filter(user=subject).first()
            if profile and profile.frozen_reason.startswith("AML review"):
                messages.error(request, "This hold belongs to an AML case. Release it from the alert.")
                return back
            kyc.unfreeze(subject, actor=request.user)
            KycProfile.objects.filter(user=subject).update(frozen_reason=f"Released by {request.user}: {reason}"[:255])
            messages.success(request, "Hold released.")
        audit(f"user.{action}", actor=request.user, obj=subject, reason=reason[:255])
    elif action in ("disable", "enable"):
        if not has_staff_role(request.user, "compliance", "operations"):
            raise PermissionDenied("Only compliance or operations can disable sign-in.")
        if subject.closed_at:
            messages.error(request, "This account is closed.")
            return back
        subject.is_active = action == "enable"
        fields = ["is_active", "updated_at"]
        if action == "disable":
            subject.token_generation += 1           # signs the person out of every app at once
            fields.append("token_generation")
        subject.save(update_fields=fields)
        audit(f"user.{action}", actor=request.user, obj=subject, reason=reason[:255])
        messages.success(request, "Sign-in disabled; all sessions ended." if action == "disable" else "Sign-in enabled.")
    else:
        messages.error(request, "Unknown action.")
    return back


# --- transactions --------------------------------------------------------------------------------
def _day(request) -> dt.date:
    day = parse_date(request.GET.get("day") or "") or timezone.localdate()
    return min(day, timezone.localdate())


@staff_role_required(*TXN_ROLES)
def transactions_page(request):
    day = _day(request)
    status = request.GET.get("status", "")
    status = status if status in transactions.STATUSES else ""
    type_key = request.GET.get("type", "")
    type_key = type_key if type_key in transactions.TYPES else ""
    flagged_only = request.GET.get("flagged") == "1"
    q = (request.GET.get("q") or "").strip()[:64]
    before = parse_datetime(request.GET.get("before") or "")
    summary = transactions.day_summary(day)
    if request.GET.get("partial") == "summary":
        return render(request, "portal/admin/_txn_summary.html",
                      {"s": summary, "labels": transactions.STATUS_LABEL, "day": day, "today": timezone.localdate()})
    rows = transactions.rows(day=day, status=status, type_key=type_key, flagged_only=flagged_only,
                             search=q, before=before, limit=50)
    return render(request, "portal/admin/transactions.html", {
        "day": day, "today": timezone.localdate(), "status": status, "type": type_key, "flagged": flagged_only,
        "q": q, "rows": rows, "s": summary, "ghs": _ghs, "labels": transactions.STATUS_LABEL,
        "types": transactions.TYPES, "statuses": transactions.STATUSES,
        "older": rows[-1]["created_at"].isoformat() if len(rows) == 50 else "",
        "can_export": has_staff_role(request.user, *EXPORT_ROLES),
    })


@staff_role_required(*EXPORT_ROLES)
def transactions_export(request):
    if not ratelimit.allow("txn_export", str(request.user.id)):
        messages.error(request, "Too many exports this hour. Please wait.")
        return redirect("portal:admin_transactions")
    day = _day(request)
    rows = transactions.rows(day=day, limit=EXPORT_MAX_ROWS)
    audit("transactions.export", actor=request.user, day=day.isoformat(), rows=len(rows))
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="sokopay-transactions-{day.isoformat()}.csv"'
    resp["Cache-Control"] = "no-store"
    w = csv.writer(resp)
    w.writerow(["created_at", "updated_at", "type", "reference", "party (masked)", "amount_ghs", "fee_ghs",
                "status", "partner_status", "flagged"])
    for r in rows:
        w.writerow([r["created_at"].isoformat(), r["updated_at"].isoformat() if r["updated_at"] else "", r["type"],
                    r["reference"], r["party"], f"{r['amount_minor'] // 100}.{r['amount_minor'] % 100:02d}",
                    f"{r['fee_minor'] // 100}.{r['fee_minor'] % 100:02d}", r["status"], r["status_raw"],
                    "yes" if r["flagged"] else ""])
    return resp


# --- payments & settlement --------------------------------------------------------------------------
@staff_role_required(*PAYMENT_ROLES)
def payments(request):
    return render(request, "portal/admin/payments_hub.html", {"p": metrics.payments_and_settlement(), "ghs": _ghs})


# --- compliance & risk ----------------------------------------------------------------------------
@staff_role_required("compliance")
def risk(request):
    from apps.compliance.models import ScreeningMatch
    from apps.merchants.models import Merchant
    high_users = (User.objects.filter(aml_alerts__status__in=metrics.OPEN_ALERT, aml_alerts__severity="high")
                  .distinct()[:20])
    for u in high_users:
        u.phone_masked = mask_phone(u.phone)
    held = KycProfile.objects.filter(frozen=True).select_related("user").order_by("-frozen_at")[:20]
    for h in held:
        h.phone_masked = mask_phone(h.user.phone)
    return render(request, "portal/admin/risk.html", {
        "c": metrics.compliance(),
        "id_docs": IdentityDocument.objects.filter(status="pending").select_related("user").order_by("created_at")[:20],
        "merchants_review": Merchant.objects.filter(status__in=("submitted", "in_review")).order_by("created_at")[:20],
        "high_users": high_users,
        "held": held,
        "high_merchants": Merchant.objects.filter(risk_tier="high", status="approved")[:20],
        "screening": ScreeningMatch.objects.filter(status__in=("pending", "confirmed")).order_by("-created_at")[:20],
        "alerts": Alert.objects.filter(status__in=metrics.OPEN_ALERT).order_by("-severity", "-last_seen")[:25],
    })


# --- insights -------------------------------------------------------------------------------------------
@staff_role_required(*INSIGHT_ROLES)
def insights(request):
    from apps.insights.models import AiBriefing, AiInteraction
    if request.method == "POST" and request.POST.get("action") == "briefing":
        if not ratelimit.allow("ai_briefing", str(request.user.id)):
            messages.error(request, "Briefings can be generated a few times an hour. Please wait.")
        else:
            try:
                ai.daily_briefing(user=request.user)
                messages.success(request, "New briefing written.")
            except ai.AiUnavailable as exc:
                messages.error(request, str(exc))
        return redirect("portal:admin_insights")
    show_log = has_staff_role(request.user, "compliance")
    return render(request, "portal/admin/insights.html", {
        "insights": anomalies.all_insights(),
        "provider": ai.provider_enabled(),
        "chat": ai.chat_enabled() and ai.user_allowed(request.user),
        "briefing": AiBriefing.objects.first(),
        "log": AiInteraction.objects.select_related("user")[:25] if show_log else None,
        "tokens_today": ai.tokens_used_today() if show_log else None,
    })


@staff_role_required(*INSIGHT_ROLES)
def insights_ask(request):
    """htmx: answer one question; returns a fragment."""
    if request.method != "POST":
        return redirect("portal:admin_insights")
    question = request.POST.get("question", "")
    try:
        result = ai.ask(request.user, question)
        ctx = {"question": question, "answer": result["answer"], "redactions": result["redactions"],
               "tools": result["tools"]}
    except ai.AiUnavailable as exc:
        ctx = {"question": question, "error": str(exc)}
    return render(request, "portal/admin/_ai_answer.html", ctx)
