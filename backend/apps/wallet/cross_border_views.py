"""
Cross-border API (customer JWT):
  GET  /cross-border                 corridors, purposes, limits (available: false until a partner is live)
  POST /cross-border/quote           {country, method, account, institution, name, amount}
  POST /cross-border/send            {quote_id, purpose, pin}
  GET  /cross-border/transfers       my sends, newest first
  GET  /cross-border/transfers/<ref>
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money, MoneyError
from apps.kyc.exceptions import KycError
from apps.portal import ratelimit

from . import cross_border as xb
from .models import CrossBorderTransfer


class _QuoteSerializer(serializers.Serializer):
    country = serializers.CharField(max_length=2)
    method = serializers.ChoiceField(choices=xb.METHODS)
    account = serializers.CharField(max_length=34)
    institution = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=120)
    amount = serializers.CharField(max_length=16)


class CorridorsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(xb.corridors())


class QuoteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not ratelimit.allow("xb_quote", str(request.user.id)):
            return Response({"error": "Too many quotes. Please wait a moment."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        form = _QuoteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        v = form.validated_data
        try:
            minor = Money.from_major(v.pop("amount"), "GHS").minor
            return Response(xb.get_quote(user=request.user, amount_minor=minor, **v))
        except (xb.CrossBorderError, MoneyError) as exc:
            return Response({"error": str(exc) or "Enter a valid amount."}, status=status.HTTP_400_BAD_REQUEST)


class _SendSerializer(serializers.Serializer):
    quote_id = serializers.CharField(max_length=64)
    purpose = serializers.CharField(max_length=32)
    pin = serializers.RegexField(r"^\d{6}$")


class SendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = _SendSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            t = xb.send(user=request.user, **form.validated_data)
        except (xb.CrossBorderError, KycError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(xb.transfer_json(t), status=status.HTTP_201_CREATED)


class TransfersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = CrossBorderTransfer.objects.filter(sender=request.user).order_by("-created_at")[:50]
        return Response({"results": [xb.transfer_json(t) for t in qs]})


class TransferDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        return Response(xb.transfer_json(get_object_or_404(CrossBorderTransfer, reference=reference,
                                                           sender=request.user)))
