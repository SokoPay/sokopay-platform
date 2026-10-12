"""
Back-office operations pages (role-gated):

  operations   /dashboard/admin/agents/            agents: register, activate, suspend
               /dashboard/admin/agents/<id>/        one agent: float, history, top-up request
               /dashboard/admin/float-topups/       maker-checker approvals (operations/finance), incl.
                                                    agents' bank deposits + slips; SokoPay deposit accounts
  finance      /dashboard/admin/commission-payouts/ fortnightly cash-out commission payouts (two people)
               /dashboard/admin/cash-outs/          cash-out request monitor
  compliance   /dashboard/admin/kyc/                tiers, held wallets, manual holds
  finance      /dashboard/admin/reconciliation/     runs and breaks; resolve with a note
  support      /dashboard/admin/support/            look a customer up (phone / wallet ID / reference)
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.common.audit import record as audit
from apps.agents import services as agents
from apps.agents.exceptions import AgentError
from apps.agents.models import Agent, CashOutRequest, DepositAccount, FloatTopUp
from apps.common.money import Money, MoneyError
from apps.licensing.exceptions import CapabilityNotLicensed

from .decorators import staff_role_required

User = get_user_model()


def _ghs(minor) -> str:
    return Money(int(minor or 0), "GHS").format()


# --- agents ----------------------------------------------------------------------------------
@staff_role_required("operations")
def agents_list(request):
    """
    Agents are only ever added here, by back-office operations staff. A new person gets
    an account created for them; either way they're registered as a PENDING agent and
    texted how to start (install the Agent app, sign in with this number). Activation
    stays a separate step after KYC checks.
    """
    if request.method == "POST":
        from apps.notifications.sms import get_sms_provider

        from . import enrolment
        try:
            agent, created = enrolment.staff_add_agent(
                actor=request.user, phone=request.POST.get("phone", ""),
                full_name=request.POST.get("full_name", ""),
                display_name=request.POST.get("display_name", ""),
                location=request.POST.get("location", ""))
        except (enrolment.EnrolmentError, AgentError) as exc:
            messages.error(request, str(exc))
            return redirect("portal:ops_agents")
        first = (agent.user.full_name or "").split(" ")[0] or "Hello"
        sms = get_sms_provider().send(
            agent.user.phone,
            f"SokoPay: {first}, you've been registered as a SokoPay agent ({agent.display_name}). "
            f"Install the SokoPay Agent app and sign in with this number. "
            f"We'll let you know when your agent account is active.")
        note = "" if sms.success else " The welcome SMS couldn't be sent."
        messages.success(request, f"{agent.display_name} registered (pending){' — new account created' if created else ''}. "
                                  f"Activate after KYC checks.{note}")
        return redirect("portal:ops_agent", pk=agent.pk)
    qs = Agent.objects.select_related("user").order_by("-created_at")
    status = request.GET.get("status")
    if status in Agent.Status.values:
        qs = qs.filter(status=status)
    rows = [(a, agents.float_balance(a)) for a in qs[:200]]
    return render(request, "portal/admin/ops_agents.html", {"rows": rows, "ghs": _ghs, "status": status})


@staff_role_required("operations")
def agent_detail(request, pk):
    agent = get_object_or_404(Agent.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "activate":
                agents.activate_agent(agent)
                audit("agent.activate", actor=request.user, obj=agent)
                messages.success(request, "Agent activated.")
            elif action == "suspend":
                agents.suspend_agent(agent, actor=request.user, reason=request.POST.get("reason", ""))
                messages.success(request, "Agent suspended.")
            elif action == "topup":
                minor = Money.from_major(request.POST.get("amount", ""), "GHS").minor
                agents.request_float_topup(agent=agent, amount_minor=minor,
                                           payment_reference=request.POST.get("payment_reference", ""),
                                           requested_by=request.user)
                messages.success(request, "Top-up recorded. A different staff member must approve it.")
        except (AgentError, MoneyError) as exc:
            messages.error(request, str(exc) or "Enter a valid amount.")
        except CapabilityNotLicensed:
            messages.error(request, "Agent operations need the DEMI licence.")
        return redirect("portal:ops_agent", pk=agent.pk)
    from apps.pricing.services import earnings
    e = earnings(agent)
    return render(request, "portal/admin/ops_agent.html", {
        "a": agent, "float": _ghs(agents.float_balance(agent)), "ghs": _ghs,
        "earn": {"redeemable": _ghs(e["redeemable_minor"]), "awaiting": _ghs(e["awaiting_payout_minor"]),
                 "paid": _ghs(e["paid_minor"])},
        "commissions": agent.commissions.select_related("txn").order_by("-created_at")[:20],
        "txns": agent.transactions.order_by("-created_at")[:30],
        "topups": agent.float_topups.select_related("requested_by", "decided_by").order_by("-created_at")[:20],
        "cashouts": agent.cash_out_requests.order_by("-created_at")[:20],
    })


@staff_role_required("operations", "finance")
def float_topups(request):
    if request.method == "POST" and request.POST.get("action") in ("add_account", "toggle_account"):
        return _deposit_account_action(request)
    if request.method == "POST":
        t = get_object_or_404(FloatTopUp, pk=request.POST.get("topup"))
        try:
            agents.decide_float_topup(t, approver=request.user, approve=request.POST.get("decision") == "approve",
                                      note=request.POST.get("note", ""))
            messages.success(request, "Decision recorded.")
        except AgentError as exc:
            messages.error(request, str(exc))
        except CapabilityNotLicensed:
            messages.error(request, "Float top-ups need the DEMI licence.")
        return redirect("portal:ops_float")
    return render(request, "portal/admin/ops_float.html", {
        "pending": FloatTopUp.objects.filter(status="pending").select_related("agent", "requested_by", "deposit_account"),
        "recent": FloatTopUp.objects.exclude(status="pending")
        .select_related("agent", "requested_by", "decided_by", "deposit_account").order_by("-decided_at")[:30],
        "accounts": DepositAccount.objects.order_by("-is_active", "bank_name"),
        "ghs": _ghs,
    })


def _deposit_account_action(request):
    """The SokoPay bank accounts agents pay cash into (shown in the Agent app). Audited."""
    if request.POST["action"] == "add_account":
        fields = {k: request.POST.get(k, "").strip() for k in ("bank_name", "account_name", "account_number", "branch")}
        if not (fields["bank_name"] and fields["account_name"] and fields["account_number"]):
            messages.error(request, "Enter the bank, account name and account number.")
        else:
            acc = DepositAccount.objects.create(**fields)
            audit("float.deposit_account_add", actor=request.user, obj=acc, bank=acc.bank_name)
            messages.success(request, f"{acc.bank_name} account added. Agents can now pay into it.")
    else:
        acc = get_object_or_404(DepositAccount, pk=request.POST.get("account"))
        acc.is_active = not acc.is_active
        acc.save(update_fields=["is_active", "updated_at"])
        audit("float.deposit_account_" + ("enable" if acc.is_active else "disable"), actor=request.user, obj=acc)
        messages.success(request, f"{acc.bank_name} {acc.account_number} {'shown to' if acc.is_active else 'hidden from'} agents.")
    return redirect("portal:ops_float")


@staff_role_required("operations", "finance")
def float_slip(request, pk):
    """An agent's deposit-slip photo, decrypted, never cached. Each view is audit-logged."""
    from django.http import Http404, HttpResponse

    from apps.common import secure_files
    t = get_object_or_404(FloatTopUp, pk=pk)
    if not t.slip_key:
        raise Http404("No slip was uploaded.")
    try:
        data, content_type, _ = secure_files.read(t.slip_key)
    except FileNotFoundError:
        raise Http404("This slip's file isn't available.") from None
    audit("float.slip_view", actor=request.user, obj=t)
    resp = HttpResponse(data, content_type=content_type)
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Disposition"] = "inline"
    return resp


@staff_role_required("finance")
def commission_payouts(request):
    """
    Cash-out commission is paid every two weeks: one finance user prepares the payout (every
    unpaid cash-out commission so far), a different one approves it and each active agent's
    float is credited. Cash-in and sales commission isn't here: agents redeem it themselves.
    """
    from apps.pricing import services as pricing
    from apps.pricing.models import AgentCommission, CommissionPayout
    if request.method == "POST":
        try:
            if request.POST.get("action") == "prepare":
                p = pricing.prepare_payout(prepared_by=request.user)
                messages.success(request, f"Payout of {_ghs(p.total_minor)} to {p.agent_count} agent(s) prepared. "
                                          "A different finance user must approve it.")
            else:
                p = get_object_or_404(CommissionPayout, pk=request.POST.get("payout"))
                p = pricing.decide_payout(p, approver=request.user, approve=request.POST.get("decision") == "approve")
                messages.success(request, f"Paid {_ghs(p.total_minor)} into agents' floats." if p.status == "paid"
                                 else "Payout rejected. The commission stays unpaid for the next one.")
        except pricing.PricingError as exc:
            messages.error(request, str(exc))
        return redirect("portal:ops_payouts")
    unpaid = AgentCommission.objects.filter(paid_at=None, payout=None, product__in=pricing.PAYOUT_PRODUCTS)
    by_agent = (unpaid.values("agent__display_name", "agent__status")
                .annotate(n=Count("id"), total=Sum("amount_minor")).order_by("-total")[:100])
    last = CommissionPayout.objects.filter(status="paid").order_by("-decided_at").first()
    pending = CommissionPayout.objects.filter(status="pending").select_related("prepared_by").first()
    pending_rows = []
    if pending:
        pending_rows = (pending.commissions.values("agent__display_name", "agent__status")
                        .annotate(n=Count("id"), total=Sum("amount_minor")).order_by("-total"))
    return render(request, "portal/admin/ops_payouts.html", {
        "by_agent": by_agent, "unpaid_total": unpaid.aggregate(s=Sum("amount_minor"))["s"] or 0,
        "pending": pending, "pending_rows": pending_rows,
        "next_due": (last.decided_at + timedelta(days=pricing.PAYOUT_INTERVAL_DAYS)) if last else None,
        "history": CommissionPayout.objects.exclude(status="pending")
        .select_related("prepared_by", "decided_by").order_by("-created_at")[:20],
        "ghs": _ghs, "interval": pricing.PAYOUT_INTERVAL_DAYS,
    })


@staff_role_required("operations")
def cash_outs(request):
    since = timezone.now() - timedelta(hours=24)
    qs = CashOutRequest.objects.select_related("agent", "customer").order_by("-created_at")
    status = request.GET.get("status")
    if status in CashOutRequest.Status.values:
        qs = qs.filter(status=status)
    counts = dict(CashOutRequest.objects.filter(created_at__gte=since)
                  .values_list("status").annotate(n=Count("id")))
    worst = (CashOutRequest.objects.filter(created_at__gte=since, status__in=("declined", "failed", "expired"))
             .values("agent__display_name").annotate(n=Count("id")).order_by("-n")[:5])
    return render(request, "portal/admin/ops_cashouts.html", {
        "rows": qs[:200], "counts": counts, "worst": worst, "ghs": _ghs, "status": status,
    })


# --- KYC (compliance) ------------------------------------------------------------------------
@staff_role_required("compliance")
def kyc_overview(request):
    from apps.kyc import services as kyc
    from apps.kyc.models import KycProfile
    if request.method == "POST":
        from apps.wallet.accounts_lookup import AccountNotFound, resolve_account
        reason = request.POST.get("reason", "").strip()
        try:
            user = resolve_account(request.POST.get("account", ""))
            if not reason:
                raise ValueError("A reason is required.")
            if request.POST.get("action") == "hold":
                kyc.freeze(user, reason=f"Manual hold: {reason}", actor=request.user)
                messages.success(request, "Wallet placed on hold.")
            else:
                profile = KycProfile.objects.filter(user=user).first()
                if profile and profile.frozen_reason.startswith("AML review"):
                    raise ValueError("This hold belongs to an AML case — release it from the alert.")
                kyc.unfreeze(user, actor=request.user)
                KycProfile.objects.filter(user=user).update(frozen_reason=f"Released by {request.user}: {reason}"[:255])
                messages.success(request, "Hold released.")
        except (AccountNotFound, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("portal:ops_kyc")
    tiers = dict(KycProfile.objects.values_list("tier").annotate(n=Count("id")))
    held = KycProfile.objects.filter(frozen=True).select_related("user", "frozen_by").order_by("-frozen_at")[:200]
    tier_rows = [(label, tiers.get(value, 0)) for value, label in KycProfile.Tier.choices]
    from apps.kyc.models import IdentityDocument
    to_review = (IdentityDocument.objects.filter(status=IdentityDocument.Status.PENDING)
                 .select_related("user").order_by("created_at")[:200])
    return render(request, "portal/admin/ops_kyc.html",
                  {"tier_rows": tier_rows, "held": held, "to_review": to_review})


@staff_role_required("compliance")
def kyc_document(request, pk):
    """Compare a passport / driver's licence photo with its details, then accept or reject."""
    from apps.kyc import documents
    from apps.kyc.exceptions import KycError
    from apps.kyc.models import IdentityDocument
    doc = get_object_or_404(IdentityDocument.objects.select_related("user", "reviewed_by"), pk=pk)
    if request.method == "POST":
        try:
            documents.review(doc, actor=request.user, approve=request.POST.get("action") == "approve",
                             note=request.POST.get("note", ""))
            messages.success(request, f"{doc.get_doc_type_display()} {doc.get_status_display().lower()}.")
            return redirect("portal:ops_kyc")
        except KycError as exc:
            messages.error(request, str(exc))
        return redirect("portal:ops_kyc_document", pk=doc.pk)
    from apps.kyc.models import KycProfile
    profile = KycProfile.objects.filter(user=doc.user).first()
    others = doc.user.identity_documents.exclude(pk=doc.pk).exclude(status=IdentityDocument.Status.REPLACED)
    return render(request, "portal/admin/ops_kyc_document.html",
                  {"doc": doc, "number": doc.number, "profile": profile, "others": others})


@staff_role_required("compliance")
def kyc_document_image(request, pk, side):
    """The decrypted photo, inline, never cached. Each view is audit-logged."""
    from django.http import Http404, HttpResponse

    from apps.common import secure_files
    from apps.kyc.models import IdentityDocument
    doc = get_object_or_404(IdentityDocument, pk=pk)
    key = doc.front_key if side == "front" else doc.back_key if side == "back" else ""
    if not key:
        raise Http404("No such photo.")
    try:
        data, content_type, _ = secure_files.read(key)
    except FileNotFoundError:
        raise Http404("This photo's file isn't available.") from None
    audit("kyc.document_view", actor=request.user, obj=doc, side=side)
    resp = HttpResponse(data, content_type=content_type)
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Disposition"] = "inline"
    return resp


# --- reconciliation (finance) ------------------------------------------------------------------
@staff_role_required("finance")
def recon_runs(request):
    from apps.reconciliation.models import ReconRun
    runs = ReconRun.objects.order_by("-date")[:60]
    return render(request, "portal/admin/ops_recon.html", {"runs": runs})


@staff_role_required("finance")
def recon_run(request, pk):
    from apps.reconciliation.models import ReconItem, ReconRun
    run = get_object_or_404(ReconRun, pk=pk)
    if request.method == "POST":
        item = get_object_or_404(ReconItem, pk=request.POST.get("item"), run=run)
        note = request.POST.get("note", "").strip()
        if not note:
            messages.error(request, "Explain how the break was resolved.")
        else:
            item.resolved, item.note = True, note[:255]
            item.resolved_by, item.resolved_at = request.user, timezone.now()
            item.save(update_fields=["resolved", "note", "resolved_by", "resolved_at", "updated_at"])
            audit("recon.resolve", actor=request.user, obj=item, note=note)
            messages.success(request, "Break resolved.")
        return redirect("portal:ops_recon_run", pk=run.pk)
    return render(request, "portal/admin/ops_recon_run.html", {
        "run": run, "items": run.items.order_by("resolved", "kind"), "ghs": _ghs})


# --- customer support lookup ---------------------------------------------------------------------
@staff_role_required("support", "compliance", "operations")
def support_lookup(request):
    q = (request.GET.get("q") or "").strip()
    user, error = None, None
    if q:
        from apps.payments.models import Payment
        from apps.wallet.accounts_lookup import AccountNotFound, resolve_account
        if q.upper().startswith("SP-"):
            p = Payment.objects.filter(reference=q.upper()).select_related("user").first()
            user = p.user if p else None
            error = None if user else "No customer payment with that reference."
        else:
            try:
                user = resolve_account(q)
            except AccountNotFound as exc:
                error = str(exc)
    context = {"q": q, "u": user, "error": error}
    if user is not None:
        if request.method == "POST" and request.POST.get("action") == "logout_all":
            from apps.accounts import tokens
            tokens.revoke_all(user)
            audit("support.logout_all", actor=request.user, obj=user, summary="Customer signed out of all devices")
            messages.success(request, "Signed out of every device.")
            return redirect(f"{request.path}?q={q}")
        from apps.activity.services import feed
        from apps.kyc.limits import profile_for
        from apps.notifications.models import Device
        from apps.wallet import services as wallet
        from apps.wallet.accounts_lookup import format_wallet_number, wallet_number_for
        profile = profile_for(user)
        context.update(
            kyc=profile, balance=_ghs(wallet.balance(user)),
            wallet_id=format_wallet_number(wallet_number_for(user)),
            activity=feed(user, period="30d", size=30)["results"],
            devices=Device.objects.filter(user=user).order_by("-created_at")[:10],
        )
    return render(request, "portal/admin/ops_support.html", context)


# --- regulatory return (finance / compliance) --------------------------------------------------
@staff_role_required("finance", "compliance")
def regulatory_return(request):
    from django.http import HttpResponse

    from apps.compliance import regulatory
    today = timezone.localdate()
    default = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")     # last month
    period = request.GET.get("month") or default
    try:
        year, month = (int(x) for x in period.split("-"))
        if not (2020 <= year <= today.year and 1 <= month <= 12):
            raise ValueError
    except ValueError:
        messages.error(request, "Choose a month like 2026-09.")
        return redirect("portal:ops_regulatory")
    report = regulatory.monthly_return(year, month)
    if request.GET.get("format") == "csv":
        audit("regulatory.export", actor=request.user, summary=f"Regulatory figures {report['period']} exported")
        resp = HttpResponse(regulatory.to_csv(report), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="sokopay-regulatory-{report["period"]}.csv"'
        resp["Cache-Control"] = "no-store"
        return resp
    return render(request, "portal/admin/ops_regulatory.html",
                  {"report": report, "rows": regulatory.flatten(report), "month": report["period"]})


# --- fees & charges: what SokoPay charges, agent commissions, discounts (finance, maker-checker) ---
GROUPS = (
    ("Customer fees", "fee", ("p2p", "transfer_momo", "transfer_bank", "bill", "airtime", "data", "cash_out",
                              "cash_in")),
    ("Merchant fees", "fee", ("bulk_momo", "bulk_bank")),
    ("Agent commissions (SokoPay pays the agent)", "commission", ("cash_in", "cash_out", "airtime", "data", "bill")),
)


def _price_type(rule) -> str:
    if rule is None:
        return "Launch default"
    if not (rule.flat_minor or rule.percent_bp):
        return "Free"
    if rule.percent_bp and not rule.flat_minor:
        return "Percentage, capped" if rule.max_minor else "Percentage"
    if rule.flat_minor and not rule.percent_bp:
        return "Flat"
    return "Flat + percentage"


def _minor_field(request, name) -> int:
    raw = (request.POST.get(name) or "").strip()
    return Money.from_major(raw, "GHS").minor if raw else 0


def _when(request, name):
    from datetime import datetime
    raw = (request.POST.get(name) or "").strip()
    return timezone.make_aware(datetime.fromisoformat(raw)) if raw else None


@staff_role_required("finance")
def pricing(request):
    from apps.pricing import services as pricing_services
    from apps.pricing.models import PriceRule, Promotion
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "propose":
                kind, product = (request.POST.get("key") or ":").split(":", 1)
                ptype = request.POST.get("ptype", "custom")
                flat, percent, lo, hi = (_minor_field(request, "flat"),
                                         int(round(float(request.POST.get("percent") or 0) * 100)),
                                         _minor_field(request, "min"), _minor_field(request, "max"))
                if ptype == "free":
                    flat = percent = lo = hi = 0
                elif ptype == "flat":
                    percent, lo, hi = 0, 0, 0
                elif ptype == "percent":
                    flat = 0
                pricing_services.propose(kind=kind, product=product, flat_minor=flat, percent_bp=percent,
                                         min_minor=lo, max_minor=hi, effective_from=_when(request, "effective_from"),
                                         note=request.POST.get("note", ""), proposed_by=request.user)
                messages.success(request, "Price change proposed. Another finance user must approve it.")
            elif action in ("approve", "reject"):
                rule = get_object_or_404(PriceRule, pk=request.POST.get("rule"))
                pricing_services.decide(rule, approver=request.user, approve=action == "approve")
                messages.success(request, "Decision recorded.")
            elif action == "promo_propose":
                free = request.POST.get("promo_type") == "free"
                pct = 10_000 if free else int(round(float(request.POST.get("percent_off") or 0) * 100))
                pricing_services.propose_promotion(
                    name=request.POST.get("name", ""), product=request.POST.get("product", ""), percent_off_bp=pct,
                    starts_at=_when(request, "starts_at") or timezone.now(), ends_at=_when(request, "ends_at"),
                    note=request.POST.get("note", ""), proposed_by=request.user)
                messages.success(request, "Discount proposed. Another finance user must approve it.")
            elif action in ("promo_approve", "promo_reject"):
                promo = get_object_or_404(Promotion, pk=request.POST.get("promo"))
                pricing_services.decide_promotion(promo, approver=request.user, approve=action == "promo_approve")
                messages.success(request, "Decision recorded.")
            elif action == "promo_end":
                promo = get_object_or_404(Promotion, pk=request.POST.get("promo"))
                pricing_services.end_promotion(promo, actor=request.user, reason=request.POST.get("reason", ""))
                messages.success(request, "Discount ended.")
        except (pricing_services.PricingError, MoneyError, ValueError, OverflowError) as exc:
            messages.error(request, str(exc) or "Check the numbers.")
        return redirect("portal:ops_pricing")

    groups = []
    for title, kind, products in GROUPS:
        rows = []
        for product in products:
            if (kind, product) not in pricing_services.DEFAULTS:
                continue
            rule = pricing_services.rule_in_force(kind, product)
            examples = []
            for amount in (100_00, 1_000_00):
                if kind == "fee":
                    q = pricing_services.quote(product, amount)
                    examples.append((_ghs(q["base"]), _ghs(q["fee"]) if q["discount"] else ""))
                else:
                    examples.append((_ghs(pricing_services.commission(product, amount)[0]), ""))
            upcoming = (PriceRule.objects.filter(kind=kind, product=product, status="approved",
                                                 effective_from__gt=timezone.now()).order_by("effective_from").first())
            rows.append({
                "key": f"{kind}:{product}", "label": PriceRule.Product(product).label, "rule": rule,
                "type": _price_type(rule), "describe": rule.describe() if rule else "launch default",
                "examples": examples, "upcoming": upcoming,
                "promotion": pricing_services.active_promotion(product) if kind == "fee" else None,
            })
        groups.append({"title": title, "kind": kind, "rows": rows})
    now = timezone.now()
    promos = Promotion.objects.select_related("proposed_by", "decided_by")
    return render(request, "portal/admin/ops_pricing.html", {
        "groups": groups,
        "fee_products": [(p, PriceRule.Product(p).label.split(" (")[0]) for p in pricing_services.FEE_PRODUCTS],
        "pending": PriceRule.objects.filter(status="pending").select_related("proposed_by").order_by("created_at"),
        "history": PriceRule.objects.exclude(status="pending").select_related("proposed_by", "decided_by")
        .order_by("-decided_at")[:40],
        "promo_running": promos.filter(status="approved", starts_at__lte=now, ends_at__gt=now).order_by("ends_at"),
        "promo_scheduled": promos.filter(status="approved", starts_at__gt=now).order_by("starts_at"),
        "promo_pending": promos.filter(status="pending").order_by("created_at"),
        "promo_history": sorted([*promos.filter(status__in=("rejected", "ended")).order_by("-updated_at")[:20],
                                 *promos.filter(status="approved", ends_at__lte=now).order_by("-ends_at")[:20]],
                                key=lambda p: p.updated_at, reverse=True),
    })

@staff_role_required("operations", "finance")
def product_withdrawals(request):
    from apps.marketplace import services as mkt
    from apps.marketplace.models import ProductTransaction
    if request.method == "POST":
        txn = get_object_or_404(ProductTransaction.objects.select_related("application__product__provider"),
                                pk=request.POST.get("txn"), kind="withdrawal", status="requested")
        try:
            if request.POST.get("action") == "reject":
                txn.status = ProductTransaction.Status.REJECTED
                txn.save(update_fields=["status", "updated_at"])
                audit("withdrawal.reject", actor=request.user, obj=txn)
                messages.success(request, "Marked as rejected by the provider.")
            else:
                minor = Money.from_major(request.POST.get("amount", ""), "GHS").minor
                mkt.confirm_withdrawal(application=txn.application, amount_minor=minor,
                                       partner_ref=request.POST.get("partner_ref", ""))
                audit("withdrawal.confirm", actor=request.user, obj=txn, amount_minor=minor,
                      partner_ref=request.POST.get("partner_ref", ""))
                messages.success(request, "Wallet credited.")
        except (mkt.MarketplaceError, MoneyError) as exc:
            messages.error(request, str(exc) or "Enter the amount the provider paid.")
        return redirect("portal:ops_withdrawals")
    rows = (ProductTransaction.objects.filter(kind="withdrawal", status="requested")
            .select_related("application__user", "application__product__provider").order_by("created_at"))
    return render(request, "portal/admin/ops_withdrawals.html", {"rows": rows})



# --- system health (any staff role) ---------------------------------------------------------
@staff_role_required("operations", "finance", "compliance", "support")
def system_health(request):
    from apps.common.health import ops_snapshot
    labels = {
        "stuck_settlements": ("Settlement payouts stuck", "portal:settlement_queue"),
        "stuck_refunds": ("MoMo refunds stuck", None),
        "transfers_pending_over_1h": ("Transfers to other networks pending over 1 hour", None),
        "cross_border_pending_over_1h": ("Cross-border sends pending over 1 hour", None),
        "lifestyle_pending_over_1h": ("Ticket / food orders pending over 1 hour", None),
        "payments_failed_15m": ("Failed payments, last 15 minutes", None),
        "payments_15m": ("All payments, last 15 minutes", None),
        "webhooks_abandoned_1h": ("Merchant webhooks given up, last hour", None),
        "aml_high_open": ("Open high-severity AML alerts", "portal:compliance"),
        "recon_breaks_open": ("Unresolved reconciliation breaks", "portal:ops_recon"),
        "disputes_overdue": ("Disputes past the merchant deadline", "portal:ops_disputes"),
        "withdrawals_waiting_over_24h": ("Provider withdrawals waiting over 24 hours", "portal:ops_withdrawals"),
        "safeguarding": ("Safeguarding (0 ok, 1 stale or missing, 2 shortfall)", "portal:safeguarding"),
    }
    snap = ops_snapshot()
    rows = [(labels[k][0], v, labels[k][1], k not in ("payments_15m",) and v > 0) for k, v in snap.items()]
    return render(request, "portal/admin/ops_health.html", {"rows": rows})



# --- audit log (compliance / finance read; CSV export is itself audited) -------------------------
@staff_role_required("compliance", "finance")
def audit_log(request):
    import csv

    from django.http import HttpResponse

    from apps.common.models import AuditEvent
    qs = AuditEvent.objects.select_related("actor").order_by("-at")
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(action__icontains=q) | Q(object_id=q) | Q(actor_label__icontains=q) | Q(request_id=q)
                       | Q(summary__icontains=q))
    if request.GET.get("format") == "csv":
        audit("audit.export", actor=request.user, summary=f"Audit log exported (filter: {q or 'none'})")
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="sokopay-audit-log.csv"'
        w = csv.writer(resp)
        w.writerow(["at", "actor", "action", "object_type", "object_id", "summary", "request_id", "ip"])
        for e in qs[:20000]:
            w.writerow([e.at.isoformat(), e.actor_label, e.action, e.object_type, e.object_id,
                        e.summary, e.request_id, e.ip])
        return resp
    return render(request, "portal/admin/ops_audit.html", {"events": qs[:300], "q": q})
