"""
Merchant portal: bulk payments (payroll / supplier runs from an uploaded file).

Thin views over apps.bulk.services. Roles: Owner/Admin/Finance upload and submit;
Owner/Finance approve (never the uploader, unless they are the only approver).
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from apps.bulk import exports, services
from apps.bulk.exceptions import BulkError
from apps.bulk.models import BulkPayout, BulkPayoutItem
from apps.bulk.parser import MAX_BYTES
from apps.merchants.settlement import available_balance_minor
from apps.licensing.capabilities import Capability
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import is_enabled

from .decorators import merchant_required


def _ghs(minor: int) -> str:
    from apps.common.money import Money
    return Money(int(minor), "GHS").format()


@merchant_required
def bulk_list(request):
    merchant = request.merchant
    if request.method == "POST":
        return _upload(request)
    context = {
        "merchant": merchant,
        "licensed": is_enabled(Capability.BULK_DISBURSEMENT),
        "can_upload": request.merchant_role in services.UPLOAD_ROLES,
        "batches": services.merchant_batches(merchant),
        "awaiting_me": services.awaiting_my_approval(merchant, request.user),
        "available_display": _ghs(available_balance_minor(merchant)),
        "max_mb": MAX_BYTES // (1024 * 1024),
    }
    return render(request, "portal/merchant/bulk.html", context)


def _upload(request):
    from . import ratelimit
    if not ratelimit.allow("bulk_upload", str(request.user.id)):
        messages.error(request, "Too many uploads in the last hour. Please wait and try again.")
        return redirect("portal:bulk")
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "Choose a .csv or .xlsx file to upload.")
        return redirect("portal:bulk")
    if upload.size > MAX_BYTES:
        messages.error(request, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller batches.")
        return redirect("portal:bulk")
    try:
        batch = services.create_batch(
            merchant=request.merchant, created_by=request.user,
            filename=upload.name, data=upload.read(), note=request.POST.get("note", ""),
        )
    except CapabilityNotLicensed:
        messages.error(request, "Bulk payments are not yet enabled on SokoPay's current licence.")
        return redirect("portal:bulk")
    except BulkError as exc:
        messages.error(request, str(exc))
        return redirect("portal:bulk")
    if batch.invalid_count:
        messages.warning(request, f"{batch.invalid_count} of {batch.total_count} rows need attention. "
                                  "Review them below before submitting.")
    else:
        messages.success(request, f"{batch.total_count} rows ready. Review and submit for approval.")
    return redirect("portal:bulk_detail", pk=batch.pk)


@merchant_required
def bulk_detail(request, pk):
    batch = get_object_or_404(BulkPayout, pk=pk, merchant=request.merchant)
    if request.method == "POST":
        return _action(request, batch)

    status_filter = request.GET.get("rows", "")
    items = batch.items.all()
    if status_filter == "problems":
        items = items.filter(status__in=(BulkPayoutItem.Status.INVALID, BulkPayoutItem.Status.FAILED))
    allowed, is_self = services.can_approve(batch, request.user)
    context = {
        "batch": batch,
        "items": items[:500],
        "shown": min(items.count(), 500),
        "rows_filter": status_filter,
        "can_submit": (request.merchant_role in services.UPLOAD_ROLES
                       and batch.status == BulkPayout.Status.DRAFT),
        "can_cancel": (request.merchant_role in services.UPLOAD_ROLES
                       and batch.status in (BulkPayout.Status.DRAFT, BulkPayout.Status.AWAITING_APPROVAL)),
        "can_approve": allowed and batch.status == BulkPayout.Status.AWAITING_APPROVAL,
        "self_approval": is_self,
        "is_maker": batch.created_by_id == request.user.id,
        "needed_display": _ghs(batch.needed_minor),
        "amount_display": _ghs(batch.total_amount_minor),
        "fee_display": _ghs(batch.total_fee_minor),
        "available_display": _ghs(available_balance_minor(batch.merchant)),
    }
    return render(request, "portal/merchant/bulk_detail.html", context)


def _action(request, batch):
    action = request.POST.get("action")
    try:
        if action == "submit":
            services.submit(batch, by=request.user,
                            exclude_invalid=request.POST.get("exclude_invalid") == "1")
            messages.success(request, "Submitted. An Owner or Finance user can now approve it.")
        elif action == "approve":
            services.approve(batch, checker=request.user, note=request.POST.get("note", ""))
            messages.success(request, "Approved. Payments are being sent — this page updates as they land.")
        elif action == "reject":
            services.reject(batch, checker=request.user, reason=request.POST.get("note", ""))
            messages.success(request, "Batch rejected. Nothing was paid.")
        elif action == "cancel":
            services.cancel(batch, by=request.user)
            messages.success(request, "Batch cancelled.")
        else:
            messages.error(request, "Unknown action.")
    except CapabilityNotLicensed:
        messages.error(request, "Bulk payments are not yet enabled on SokoPay's current licence.")
    except BulkError as exc:
        messages.error(request, str(exc))
    return redirect("portal:bulk_detail", pk=batch.pk)


@merchant_required
def bulk_progress(request, pk):
    """HTMX partial polled while a batch is paying out."""
    batch = get_object_or_404(BulkPayout, pk=pk, merchant=request.merchant)
    return render(request, "portal/merchant/_bulk_progress.html", {"batch": batch})


@merchant_required
@require_GET
def bulk_results(request, pk):
    batch = get_object_or_404(BulkPayout, pk=pk, merchant=request.merchant)
    resp = HttpResponse(exports.results_csv(batch), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="sokopay-bulk-{str(batch.id)[:8]}-results.csv"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@merchant_required
@require_GET
def bulk_sample(request, fmt):
    if fmt == "csv":
        body, ctype = exports.sample_csv(), "text/csv; charset=utf-8"
    elif fmt == "xlsx":
        body, ctype = exports.sample_xlsx(), \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise Http404
    resp = HttpResponse(body, content_type=ctype)
    resp["Content-Disposition"] = f'attachment; filename="sokopay-bulk-payment-template.{fmt}"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp
