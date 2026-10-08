"""
DEVELOPMENT / DEMO ONLY: play the payment partner and the customer's phone.

/dev/mock-partner/ lists everything waiting on the (mock) partner: MoMo payments waiting
for the customer to approve the prompt, settlement and refund payouts, cross-border sends.
"Approve" / "Decline" tell the mock partner the outcome, then run the same status check
the background pollers run, so the rest of the system reacts exactly as it would to a real
partner (ledger, notifications, webhooks, receipts).

Mounted only when mock integrations are allowed AND either DEBUG (local) or STAGING_TOOLS
(a staging server) is on; on a server it also needs a superuser who passed 2FA. Production
forces ALLOW_MOCK_INTEGRATIONS off, so it can never exist there.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.rails.types import RailStatus


def enabled() -> bool:
    """Local development (DEBUG), or a staging server (STAGING_TOOLS), and mocks allowed."""
    return bool(getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False)
                and (settings.DEBUG or getattr(settings, "STAGING_TOOLS", False)))


def _allowed(request) -> bool:
    """On a server (DEBUG off) only a superuser who passed 2FA may play the partner."""
    if settings.DEBUG:
        return True
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser
                and getattr(user, "is_verified", lambda: False)())


def _settle(kind: str, pk: str, outcome: RailStatus) -> str:
    from apps.connectors.cross_border import MockCrossBorderConnector
    from apps.merchants import refunds, settlement
    from apps.merchants.models import Refund, Settlement
    from apps.payments import services as payments
    from apps.payments.models import Payment
    from apps.rails.mock import MockRail
    from apps.wallet import cross_border
    from apps.wallet.models import CrossBorderTransfer
    if kind == "payment":
        p = Payment.objects.get(pk=pk, status=Payment.Status.PENDING)
        MockRail.drive(p.rail_ref, outcome)
        if outcome == RailStatus.SUCCEEDED:
            payments._on_collection_success(p)
        else:
            payments.fail_payment(p, "rail_declined")
        return p.reference
    if kind == "settlement":
        s = Settlement.objects.get(pk=pk, status=Settlement.Status.PROCESSING)
        MockRail.drive(s.rail_ref, outcome)
        settlement.refresh_settlement(s)
        return f"settlement {str(s.id)[:8]}"
    if kind == "refund":
        r = Refund.objects.get(pk=pk, status=Refund.Status.PROCESSING)
        MockRail.drive(r.rail_ref, outcome)
        refunds.refresh(r)
        return f"refund {str(r.id)[:8]}"
    if kind == "cross_border":
        t = CrossBorderTransfer.objects.get(pk=pk, status="pending")
        MockCrossBorderConnector.drive(t.provider_ref, outcome)
        cross_border.apply_outcome(t, outcome)
        return t.reference
    raise Http404


@require_http_methods(["GET", "POST"])
def mock_partner(request):
    if not enabled():
        raise Http404
    if not _allowed(request):
        return redirect(f"/dashboard/login/?next={request.path}")
    from apps.merchants.models import Refund, Settlement
    from apps.payments.models import Payment
    from apps.wallet.models import CrossBorderTransfer
    if request.method == "POST":
        outcome = RailStatus.SUCCEEDED if request.POST.get("outcome") == "approve" else RailStatus.FAILED
        try:
            what = _settle(request.POST.get("kind", ""), request.POST.get("id", ""), outcome)
            messages.success(request, f"{what}: {'approved' if outcome == RailStatus.SUCCEEDED else 'declined'}.")
        except Exception as exc:   # noqa: BLE001 - dev tool: show whatever went wrong
            messages.error(request, f"Couldn't update: {exc}")
        return redirect("dev-mock-partner")
    return render(request, "dev/mock_partner.html", {
        "payments": Payment.objects.filter(status="pending").exclude(rail_ref="").select_related("merchant", "user")
        .order_by("-created_at")[:50],
        "settlements": Settlement.objects.filter(status="processing").exclude(rail_ref="").select_related("merchant"),
        "refunds": Refund.objects.filter(status="processing").exclude(rail_ref="").select_related("payment"),
        "transfers": CrossBorderTransfer.objects.filter(status="pending").exclude(provider_ref=""),
    })
