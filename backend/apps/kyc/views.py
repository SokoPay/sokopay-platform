"""
KYC API (JWT):
  GET  /kyc           → tier, limits, usage, frozen
  POST /kyc/upgrade   {ghana_card_number}   → tier 0 → 1
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money

from . import limits, services
from .exceptions import KycError


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


def _state(user) -> dict:
    profile = limits.profile_for(user)
    tier = limits.tier_limits(profile.tier)
    used = limits.usage(user)
    return {
        "tier": profile.tier,
        "tier_name": tier["name"],
        "ghana_card": profile.ghana_card_masked,
        "frozen": profile.frozen,
        "limits": {k: limits.format_limit(tier[k])
                   for k in ("max_balance", "max_txn", "daily_out", "monthly_out")},
        "usage": {k: _ghs(v) for k, v in used.items()},
        "next_step": _next_step(profile.tier),
    }


def _next_step(tier: int) -> str:
    return {0: "ghana_card", 1: "selfie"}.get(tier, "")


class KycView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_state(request.user))


class _UpgradeSerializer(serializers.Serializer):
    ghana_card_number = serializers.CharField(max_length=20)


class KycUpgradeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = _UpgradeSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            services.upgrade_to_verified(request.user, form.validated_data["ghana_card_number"])
        except KycError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_state(request.user))


class KycSelfieView(APIView):
    """POST /kyc/upgrade/selfie — multipart with a `selfie` image file (tier 1 → 2)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        upload = request.FILES.get("selfie")
        if upload is None:
            return Response({"error": "Attach your selfie as 'selfie'."},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > services.MAX_SELFIE_BYTES:
            return Response({"error": "Selfie must be under 5 MB."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            services.upgrade_to_enhanced(request.user, upload.read())
        except KycError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_state(request.user))
