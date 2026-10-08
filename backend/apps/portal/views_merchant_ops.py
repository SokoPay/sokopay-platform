"""
Merchant portal: payment detail + refunds, disputes, webhooks.  Staff: dispute decisions.

  /dashboard/payments/<reference>/      detail; Owner/Finance can refund (password session + 2FA)
  /dashboard/disputes/                  answer customer disputes (refund or explain)
  /dashboard/webhooks/                  endpoints, signing secret, test, delivery log
  /dashboard/admin/disputes/            operations: overdue / answered disputes to decide
  /dashboard/admin/disputes/<id>/
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.common.audit import record as audit
from apps.common.money import Money, MoneyError
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.merchants import disputes, refunds, webhooks
from apps.merchants.exceptions import DisputeError, RefundError
from apps.merchants.models import Dispute, WebhookDelivery, WebhookEndpoint
from apps.payments.models import Payment

from . import ratelimit
from .decorators import merchant_required, staff_role_required

MOVE_MONEY = ("owner", "finance")
WEBHOOK_ROLES = ("owner", "admin", "developer")


def _minor_or_none(raw: str):
    raw = (raw or "").strip()
    return Money.from_major(raw, "GHS").minor if raw else None


@merchant_required
def payment_detail(request, reference):
    p = get_object_or_404(Payment, reference=reference, merchant=request.merchant)
    if request.merchant_role not in ("owner", "admin", "finance"):
        messages.error(request, "Your role can't see payment details.")
        return redirect("portal:payments")
    active = p.disputes.filter(status__in=Dispute.ACTIVE).first()
    if request.method == "POST":
        if request.merchant_role not in MOVE_MONEY:
            messages.error(request, "Only an Owner or Finance user can refund.")
        elif active:
            messages.error(request, "This payment has an open dispute. Answer it under Disputes.")
        elif not ratelimit.allow("merchant_refund", str(request.user.id)):
            messages.error(request, "Too many refunds in a short time. Please wait.")
        else:
            try:
                r = refunds.create_refund(payment=p, amount_minor=_minor_or_none(request.POST.get("amount")),
                                          reason=request.POST.get("reason", ""), requested_by=request.user)
                messages.success(request, f"Refund {r.get_status_display().lower()}.")
            except (RefundError, MoneyError) as exc:
                messages.error(request, str(exc) or "Enter a valid amount.")
            except CapabilityNotLicensed:
                messages.error(request, "Refunds need the PSP Medium licence.")
        return redirect("portal:payment_detail", reference=p.reference)
    refundable = refunds.refundable_minor(p) if p.mode == "live" else 0
    return render(request, "portal/merchant/payment_detail.html", {
        "p": p, "refunds": p.refunds.select_related("requested_by").order_by("-created_at"),
        "refundable": refundable, "dispute": active, "net": p.amount_minor - p.fee_minor,
        "can_refund": refundable > 0 and not active and request.merchant_role in MOVE_MONEY,
    })


@merchant_required
def disputes_page(request):
    m = request.merchant
    if request.merchant_role not in ("owner", "admin", "finance"):
        messages.error(request, "Your role can't see disputes.")
        return redirect("portal:dashboard")
    if request.method == "POST":
        d = get_object_or_404(Dispute, pk=request.POST.get("dispute"), merchant=m)
        if request.merchant_role not in MOVE_MONEY:
            messages.error(request, "Only an Owner or Finance user can answer disputes.")
        else:
            try:
                disputes.merchant_respond(d, by=request.user, accept=request.POST.get("action") == "refund",
                                          response=request.POST.get("response", ""))
                messages.success(request, "Answer recorded.")
            except DisputeError as exc:
                messages.error(request, str(exc))
        return redirect("portal:disputes")
    return render(request, "portal/merchant/disputes.html", {
        "active": Dispute.objects.filter(merchant=m, status__in=Dispute.ACTIVE).select_related("payment")
        .order_by("respond_by"),
        "closed": Dispute.objects.filter(merchant=m).exclude(status__in=Dispute.ACTIVE).select_related("payment")
        .order_by("-updated_at")[:30],
        "held": disputes.held_minor(m), "can_answer": request.merchant_role in MOVE_MONEY,
    })


@merchant_required
def webhooks_page(request):
    m = request.merchant
    if request.merchant_role not in WEBHOOK_ROLES:
        messages.error(request, "Your role can't manage webhooks.")
        return redirect("portal:dashboard")
    shown_secret = None
    if request.method == "POST":
        action = request.POST.get("action")
        ep = None
        if request.POST.get("endpoint"):
            ep = get_object_or_404(WebhookEndpoint, pk=request.POST["endpoint"], merchant=m)
        try:
            if action == "add":
                ep = webhooks.add_endpoint(merchant=m, url=request.POST.get("url", ""),
                                           events=request.POST.getlist("events"), created_by=request.user)
                shown_secret = ep.secret
                messages.success(request, "Webhook added. Copy the signing secret now; it is shown only once.")
            elif action == "rotate" and ep:
                shown_secret = webhooks.rotate_secret(ep)
                audit("webhook.rotate_secret", actor=request.user, obj=ep, url=ep.url)
                messages.success(request, "New signing secret created. Update your server now.")
            elif action == "toggle" and ep:
                if not ep.active:
                    webhooks.check_url(ep.url)
                ep.active = not ep.active
                ep.consecutive_failures, ep.disabled_reason = 0, ""
                ep.save(update_fields=["active", "consecutive_failures", "disabled_reason", "updated_at"])
                messages.success(request, "Webhook " + ("switched on." if ep.active else "switched off."))
            elif action == "delete" and ep:
                audit("webhook.delete", actor=request.user, obj=ep, url=ep.url)
                ep.delete()
                messages.success(request, "Webhook removed.")
            elif action == "test" and ep:
                if not ratelimit.allow("webhook_test", str(request.user.id)):
                    messages.error(request, "Too many tests. Please wait.")
                else:
                    d = webhooks.send_test(ep)
                    if d.status == WebhookDelivery.Status.SUCCEEDED:
                        messages.success(request, f"Test delivered (HTTP {d.last_status_code}).")
                    else:
                        messages.error(request, f"Test failed: {d.last_error}")
        except webhooks.WebhookUrlError as exc:
            messages.error(request, str(exc))
        if not shown_secret:
            return redirect("portal:webhooks")
    endpoints = m.webhook_endpoints.order_by("created_at")
    return render(request, "portal/merchant/webhooks.html", {
        "endpoints": endpoints, "events": WebhookEndpoint.EVENTS, "shown_secret": shown_secret,
        "deliveries": WebhookDelivery.objects.filter(endpoint__merchant=m).select_related("endpoint")
        .order_by("-created_at")[:50],
    })


# --- staff -----------------------------------------------------------------------------------
@staff_role_required("operations")
def staff_disputes(request):
    return render(request, "portal/admin/ops_disputes.html", {
        "queue": disputes.for_staff_queue().select_related("payment", "merchant", "customer").order_by("respond_by"),
        "waiting": Dispute.objects.filter(status=Dispute.Status.OPEN).count(),
    })


@staff_role_required("operations")
def staff_dispute_detail(request, pk):
    d = get_object_or_404(Dispute.objects.select_related("payment", "merchant", "customer"), pk=pk)
    if request.method == "POST":
        try:
            disputes.decide(d, staff=request.user, for_customer=request.POST.get("decision") == "customer",
                            note=request.POST.get("note", ""))
            messages.success(request, "Decision recorded.")
        except DisputeError as exc:
            messages.error(request, str(exc))
        return redirect("portal:ops_dispute", pk=d.pk)
    return render(request, "portal/admin/ops_dispute.html", {
        "d": d, "refunds": d.payment.refunds.order_by("-created_at"),
        "other_disputes": Dispute.objects.filter(customer=d.customer).exclude(pk=d.pk).count(),
    })


@merchant_required
def statements_page(request):
    """Owner/Admin/Finance: download the merchant balance statement as PDF or CSV."""
    from apps.activity.statement_views import file_response
    from apps.ledger import statements as st
    if request.merchant_role not in ("owner", "admin", "finance"):
        messages.error(request, "Your role can't download statements.")
        return redirect("portal:dashboard")
    fmt = request.GET.get("format")
    if fmt in ("csv", "pdf"):
        if not ratelimit.allow("statement", str(request.user.id)):
            messages.error(request, "Too many statements requested. Please wait.")
            return redirect("portal:statements")
        try:
            start, end = st.parse_period(request.GET.get("from"), request.GET.get("to"))
        except st.StatementError as exc:
            messages.error(request, str(exc))
            return redirect("portal:statements")
        return file_response(st.merchant_statement(request.merchant, start, end), fmt, "merchant")
    from django.utils import timezone
    today = timezone.localdate()
    return render(request, "portal/merchant/statements.html",
                  {"default_from": today.replace(day=1).isoformat(), "default_to": today.isoformat()})


@merchant_required
def payment_links(request):
    """Owner/Admin/Finance create shareable payment links; anyone on the team can see them."""
    from apps.merchants import hosted, qr
    from apps.merchants.models import PaymentLink
    m = request.merchant
    if request.method == "POST":
        if request.merchant_role not in ("owner", "admin", "finance"):
            messages.error(request, "Your role can't manage payment links.")
        elif request.POST.get("action") == "toggle":
            link = get_object_or_404(PaymentLink, pk=request.POST.get("link"), merchant=m)
            link.active = not link.active
            link.save(update_fields=["active", "updated_at"])
        else:
            try:
                raw_uses = (request.POST.get("max_uses") or "").strip()
                hosted.create_link(merchant=m, title=request.POST.get("title", ""),
                                   amount_minor=_minor_or_none(request.POST.get("amount")),
                                   description=request.POST.get("description", ""),
                                   max_uses=int(raw_uses) if raw_uses.isdigit() and int(raw_uses) > 0 else None,
                                   created_by=request.user)
                messages.success(request, "Payment link created.")
            except (hosted.CheckoutError, MoneyError) as exc:
                messages.error(request, str(exc) or "Enter a valid amount.")
        return redirect("portal:payment_links")
    rows = [(link, hosted.link_url(link), qr.qr_svg_data_uri(hosted.link_url(link), scale=3))
            for link in m.payment_links.order_by("-created_at")[:50]]
    return render(request, "portal/merchant/payment_links.html", {"rows": rows})
