"""
Merchant API views.

Authenticated with a merchant API key (see auth.py). This is the external API a
merchant's own systems call to collect payments. It is separate from the consumer API
in apps.payments and from the (later) merchant portal UI.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.models import Payment
from apps.payments.serializers import PaymentSerializer
from apps.rails.types import Network

from . import checkout
from .auth import MerchantApiKeyAuthentication
from .exceptions import MerchantError
from .serializers import MerchantChargeRequest


class MerchantChargeView(APIView):
    """POST /api/v1/merchant/charges — collect a payment for the calling merchant."""

    authentication_classes = [MerchantApiKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = MerchantChargeRequest(data=request.data)
        form.is_valid(raise_exception=True)
        api_key = request.auth  # the ApiKey from authentication
        try:
            payment = checkout.initiate_merchant_charge(
                merchant=api_key.merchant,
                amount_minor=form.amount_minor(),
                network=Network(form.validated_data["network"]),
                payer=form.validated_data["payer"],
                narrative=form.validated_data.get("narrative", ""),
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except MerchantError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # (CapabilityNotLicensed -> 403 is handled by the project exception handler.)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class MerchantChargeStatusView(APIView):
    authentication_classes = [MerchantApiKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        payment = get_object_or_404(
            Payment, reference=reference, merchant=request.auth.merchant
        )
        return Response(PaymentSerializer(payment).data)
