"""
Refund and dispute API.

Customer (normal app JWT):
  GET  /disputes                                  my disputes
  POST /disputes          {reference, reason, description, amount?}
  POST /disputes/<id>/withdraw

Merchant app (team members; refunds need Owner/Finance + PIN step-up):
  POST /merchant-app/payments/<reference>/refund  {amount?, reason, pin}
  GET  /merchant-app/disputes?status=active|closed
  POST /merchant-app/disputes/<id>/respond        {accept, response, pin (when accept)}
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money
from apps.portal import ratelimit

from . import disputes, refunds
from .app_views import MOVE_MONEY_ROLES, VIEW_MONEY_ROLES, AmountField, MerchantAppView
from .exceptions import DisputeError, RefundError
from .models import Dispute, Refund


def _ghs(minor) -> str:
    return Money(int(minor or 0), "GHS").format()


def dispute_json(d: Dispute, *, for_merchant: bool = False) -> dict:
    body = {
        "id": str(d.id), "payment_reference": d.payment.reference, "status": d.status,
        "status_display": d.get_status_display(), "reason": d.reason, "reason_display": d.get_reason_display(),
        "description": d.description, "amount_minor": d.amount_minor, "amount_display": _ghs(d.amount_minor),
        "merchant_name": d.merchant.trading_name or d.merchant.legal_name,
        "respond_by": d.respond_by.isoformat(), "merchant_response": d.merchant_response,
        "decision_note": d.decision_note if not d.is_active else "",
        "created_at": d.created_at.isoformat(),
    }
    if for_merchant:
        body["customer"] = d.payment.payer_masked
    return body


def refund_json(r: Refund) -> dict:
    return {"id": str(r.id), "amount_minor": r.amount_minor, "amount_display": _ghs(r.amount_minor),
            "status": r.status, "status_display": r.get_status_display(), "reason": r.reason,
            "destination": r.get_destination_display(), "created_at": r.created_at.isoformat()}


def customer_dispute_summary(payment) -> dict:
    """Extra receipt fields for a merchant payment, as seen by the customer."""
    active = payment.disputes.filter(status__in=Dispute.ACTIVE).first()
    latest = active or payment.disputes.order_by("-created_at").first()
    refundable = refunds.refundable_minor(payment)
    return {
        "refunded_minor": refunds.refunded_minor(payment),
        "can_dispute": bool(refundable and active is None and payment.status == "succeeded"
                            and payment.mode == "live"),
        "dispute": dispute_json(latest) if latest else None,
    }


# --- customer --------------------------------------------------------------------------------
class OpenDisputeSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=16)
    reason = serializers.ChoiceField(choices=Dispute.Reason.choices)
    description = serializers.CharField(max_length=1000)
    amount = AmountField(required=False, allow_null=True)


class CustomerDisputesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Dispute.objects.filter(customer=request.user).select_related("payment", "merchant") \
            .order_by("-created_at")[:50]
        return Response({"results": [dispute_json(d) for d in qs]})

    def post(self, request):
        if not ratelimit.allow("dispute_open", str(request.user.id)):
            return Response({"error": "Too many attempts. Please wait and try again."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        form = OpenDisputeSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        v = form.validated_data
        try:
            d = disputes.open_dispute(customer=request.user, reference=v["reference"], reason=v["reason"],
                                      description=v["description"], amount_minor=v.get("amount"))
        except DisputeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(dispute_json(d), status=status.HTTP_201_CREATED)


class CustomerDisputeWithdrawView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        d = get_object_or_404(Dispute, pk=pk, customer=request.user)
        try:
            d = disputes.withdraw(d, customer=request.user)
        except DisputeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(dispute_json(d))


# --- merchant app ------------------------------------------------------------------------------
class RefundSerializer(serializers.Serializer):
    amount = AmountField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=255)
    pin = serializers.RegexField(r"^\d{6}$")


class MerchantRefundView(MerchantAppView):
    allowed_roles = MOVE_MONEY_ROLES

    def post(self, request, reference):
        from apps.payments.models import Payment
        if (resp := self._throttled(request, "merchant_refund")):
            return resp
        p = get_object_or_404(Payment, reference=reference, merchant=request.merchant)
        form = RefundSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        if (resp := self._step_up(request)):
            return resp
        if p.disputes.filter(status__in=Dispute.ACTIVE).exists():
            return Response({"error": "This payment has an open dispute. Answer it in Disputes instead."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            r = refunds.create_refund(payment=p, amount_minor=form.validated_data.get("amount"),
                                      reason=form.validated_data["reason"], requested_by=request.user)
        except RefundError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(refund_json(r), status=status.HTTP_201_CREATED)


class MerchantDisputesView(MerchantAppView):
    allowed_roles = VIEW_MONEY_ROLES

    def get(self, request):
        qs = Dispute.objects.filter(merchant=request.merchant).select_related("payment", "merchant")
        if request.query_params.get("status") == "closed":
            qs = qs.exclude(status__in=Dispute.ACTIVE)
        else:
            qs = qs.filter(status__in=Dispute.ACTIVE)
        return Response({
            "results": [dispute_json(d, for_merchant=True) for d in qs.order_by("respond_by")[:100]],
            "held_display": _ghs(disputes.held_minor(request.merchant)),
            "can_respond": request.member.role in MOVE_MONEY_ROLES,
        })


class RespondSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
    response = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    pin = serializers.RegexField(r"^\d{6}$", required=False)


class MerchantDisputeRespondView(MerchantAppView):
    allowed_roles = VIEW_MONEY_ROLES

    def post(self, request, pk):
        if request.member.role not in MOVE_MONEY_ROLES:
            raise PermissionDenied("Only an Owner or Finance user can answer disputes.")
        if (resp := self._throttled(request, "merchant_refund")):
            return resp
        d = get_object_or_404(Dispute, pk=pk, merchant=request.merchant)
        form = RespondSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        accept = form.validated_data["accept"]
        if accept and (resp := self._step_up(request)):     # money leaves: PIN again
            return resp
        try:
            d = disputes.merchant_respond(d, by=request.user, accept=accept,
                                          response=form.validated_data.get("response", ""))
        except DisputeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(dispute_json(d, for_merchant=True))
