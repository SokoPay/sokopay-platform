"""
Back-office: fraud & AML case work (compliance staff only).

  /dashboard/admin/compliance/                 alert queue (filters)
  /dashboard/admin/compliance/alerts/<id>/     one case: subject, evidence, timeline, actions
  /dashboard/admin/compliance/screening/       watchlist matches to confirm / clear
  /dashboard/admin/compliance/reports/         STRs + large-transaction export
  /dashboard/admin/compliance/reports/<id>/    one STR: approve (2nd officer), file, download
  /dashboard/admin/compliance/watchlists/      list status + upload a new list

Thin views over apps.compliance; every action is recorded on the alert's timeline.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.common.audit import record as audit
from apps.compliance import alerts as alert_service
from apps.compliance import reports, screening, watchlists
from apps.compliance.models import (
    Alert,
    LargeTransactionReport,
    ScreeningMatch,
    SuspiciousTransactionReport,
    WatchlistEntry,
    WatchlistImport,
)

from .decorators import staff_role_required

compliance_only = staff_role_required("compliance")
OPEN = (Alert.Status.OPEN, Alert.Status.INVESTIGATING, Alert.Status.ESCALATED)


@compliance_only
def queue(request):
    status = request.GET.get("status", "open")
    qs = Alert.objects.all()
    if status == "open":
        qs = qs.filter(status__in=OPEN)
    elif status in Alert.Status.values:
        qs = qs.filter(status=status)
    if request.GET.get("severity") in ("low", "medium", "high"):
        qs = qs.filter(severity=request.GET["severity"])
    if request.GET.get("rule"):
        qs = qs.filter(rule=request.GET["rule"])
    order = {"high": 0, "medium": 1, "low": 2}
    alerts = sorted(qs.select_related("assigned_to")[:300],
                    key=lambda a: (order.get(a.severity, 3), -a.last_seen.timestamp()))
    return render(request, "portal/admin/compliance_queue.html", {
        "alerts": alerts,
        "status": status,
        "rules": sorted(set(Alert.objects.values_list("rule", flat=True))),
        "counts": {s: Alert.objects.filter(status__in=OPEN, severity=s).count() for s in ("high", "medium", "low")},
        "pending_matches": ScreeningMatch.objects.filter(status="pending").count(),
    })


@compliance_only
def alert_detail(request, pk):
    alert = get_object_or_404(Alert, pk=pk)
    if request.method == "POST":
        return _alert_action(request, alert)
    context = {"a": alert, "notes": alert.notes.select_related("author"), "frozen": None, "activity": []}
    if alert.user is not None:
        from apps.activity.services import feed
        from apps.kyc.limits import profile_for
        from apps.wallet import services as wallet
        profile = profile_for(alert.user)
        context.update(
            frozen=profile.frozen, kyc=profile,
            balance=wallet.balance(alert.user) / 100,
            activity=feed(alert.user, period="30d", size=30)["results"],
        )
    return render(request, "portal/admin/compliance_alert.html", context)


def _alert_action(request, alert):
    action, text = request.POST.get("action"), request.POST.get("text", "")
    try:
        if action == "assign":
            alert_service.assign(alert, actor=request.user)
        elif action == "note":
            alert_service.add_note(alert, author=request.user, text=text)
        elif action == "escalate":
            alert_service.escalate(alert, actor=request.user, reason=text)
        elif action == "hold":
            alert_service.place_hold(alert, actor=request.user, reason=text or "Under investigation")
        elif action == "release":
            alert_service.release_hold(alert, actor=request.user, reason=text)
        elif action == "close":
            alert_service.close(alert, actor=request.user, reason=text)
        elif action == "str":
            report = reports.draft_str(alerts=[alert], prepared_by=request.user, narrative=text)
            messages.success(request, "STR drafted. A second compliance officer must approve it.")
            return redirect("portal:compliance_str", pk=report.pk)
        else:
            messages.error(request, "Unknown action.")
            return redirect("portal:compliance_alert", pk=alert.pk)
        messages.success(request, "Done.")
    except (ValueError, reports.ReportError) as exc:
        messages.error(request, str(exc))
    return redirect("portal:compliance_alert", pk=alert.pk)


@compliance_only
def screening_queue(request):
    if request.method == "POST":
        m = get_object_or_404(ScreeningMatch, pk=request.POST.get("match"), status="pending")
        try:
            screening.review(m, actor=request.user, confirmed=request.POST.get("decision") == "confirm",
                             reason=request.POST.get("reason", ""))
            messages.success(request, "Decision recorded.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("portal:compliance_screening")
    matches = ScreeningMatch.objects.filter(status="pending").select_related("user", "entry").order_by("-score")
    return render(request, "portal/admin/compliance_screening.html", {"matches": matches})


@compliance_only
def reports_list(request):
    return render(request, "portal/admin/compliance_reports.html", {
        "reports": SuspiciousTransactionReport.objects.select_related("prepared_by", "approved_by")
        .order_by("-created_at")[:100],
        "ltr_pending": LargeTransactionReport.objects.filter(exported_at__isnull=True).count(),
    })


@compliance_only
def str_detail(request, pk):
    report = get_object_or_404(SuspiciousTransactionReport, pk=pk)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "approve":
                reports.approve_str(report, approver=request.user)
                messages.success(request, "Approved — ready to file with the FIC.")
            elif request.POST.get("action") == "file":
                reports.mark_filed(report, actor=request.user, fic_reference=request.POST.get("fic_reference", ""))
                messages.success(request, "Marked as filed; linked alerts closed as reported.")
        except reports.ReportError as exc:
            messages.error(request, str(exc))
        return redirect("portal:compliance_str", pk=report.pk)
    return render(request, "portal/admin/compliance_str.html", {"r": report, "alerts": report.alerts.all()})


@compliance_only
def str_download(request, pk):
    report = get_object_or_404(SuspiciousTransactionReport, pk=pk)
    audit("str.download", actor=request.user, obj=report, summary="STR exported")
    resp = HttpResponse(reports.export_str_json(report), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="sokopay-str-{str(report.id)[:8]}.json"'
    return resp


@compliance_only
@require_POST
def ltr_export(request):
    audit("ltr.export", actor=request.user, summary="Large transaction report exported")
    resp = HttpResponse(reports.export_large_txns_csv(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="sokopay-large-transactions.csv"'
    return resp


@compliance_only
def watchlists_page(request):
    if request.method == "POST":
        f = request.FILES.get("file")
        if f is None:
            messages.error(request, "Choose a file.")
        elif f.size > watchlists.MAX_FILE_BYTES:
            messages.error(request, "File is too large.")
        else:
            try:
                rec = watchlists.import_list(source=request.POST.get("source", ""), fmt=request.POST.get("format", ""),
                                             filename=f.name, data=f.read(), actor=request.user)
                messages.success(request, f"Imported {rec.entries} entries. Everyone is being rescreened.")
            except watchlists.WatchlistError as exc:
                messages.error(request, str(exc))
        return redirect("portal:compliance_watchlists")
    loaded = [(label, WatchlistEntry.objects.filter(source=value).count())
              for value, label in WatchlistEntry.Source.choices]
    return render(request, "portal/admin/compliance_watchlists.html", {
        "loaded": loaded, "sources": WatchlistEntry.Source.choices, "formats": sorted(watchlists.PARSERS),
        "imports": WatchlistImport.objects.select_related("imported_by").order_by("-created_at")[:20],
    })
