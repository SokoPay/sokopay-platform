"""
GET /api/v1/fees/quote?product=p2p&amount=10.00 → the fee the customer will pay, itemised,
so the app can show it before the PIN prompt (it's charged at the price in force when
the payment is made, which is what this returns).
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money

from . import services

QUOTABLE = ("p2p", "transfer_momo", "transfer_bank", "bill", "airtime", "data", "cash_out")


class _QuoteSerializer(serializers.Serializer):
    product = serializers.ChoiceField(choices=QUOTABLE)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class FeeQuoteView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        form = _QuoteSerializer(data=request.query_params)
        if not form.is_valid():
            return Response({"error": "Give a product and an amount."}, status=status.HTTP_400_BAD_REQUEST)
        amount = Money.from_major(form.validated_data["amount"], "GHS").minor
        q = services.quote(form.validated_data["product"], amount)
        fmt = lambda m: Money(m, "GHS").format()  # noqa: E731
        return Response({
            "product": form.validated_data["product"],
            "amount": fmt(amount), "fee": fmt(q["fee"]), "total": fmt(amount + q["fee"]),
            "fee_minor": q["fee"], "base_fee": fmt(q["base"]), "discount": fmt(q["discount"]),
            "promotion": q["promotion"],
        })
