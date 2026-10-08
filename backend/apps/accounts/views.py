"""
Consumer authentication API for the mobile apps.

Endpoints (all under /api/v1/auth/):
  POST otp/request   {phone}                 → sends an SMS code
  POST otp/verify    {phone, code}           → {next: set_pin|home|pin_login, tokens?}
                     (an existing PIN holder must ALSO enter their PIN: SIM-swap protection)
  POST pin/set       {pin}        (auth)     → creates the FIRST 6-digit PIN
  POST pin/change    {current_pin, new_pin} (auth) → new tokens, other devices out
  POST pin/forgot    {phone}                 → SMS reset code (same reply either way)
  POST pin/reset     {phone, code, new_pin, ghana_card?} → tokens (24 h send cap)
  GET/PATCH profile  {full_name?, email?} (auth)
  POST close         {pin, reason?} (auth)   → closes the account
  POST login         {phone, pin}            → {tokens}
  POST refresh       {refresh}               → {tokens}   (old refresh token retired)
  POST logout        {refresh?}  (auth)      → revokes this session's tokens
  POST logout-all                (auth)      → signs out of every device
  GET  me                         (auth)     → the current user
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.sms import get_sms_provider

from . import services
from .otp import OtpError
from .serializers import (
    CloseAccountSerializer,
    LogoutSerializer,
    MeSerializer,
    PinChangeSerializer,
    PinResetSerializer,
    ProfileSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    PinLoginSerializer,
    PinSetSerializer,
    RefreshSerializer,
)

logger = logging.getLogger("sokopay.auth")


class OtpRequestView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        form = OtpRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        phone = form.validated_data["phone"]
        try:
            code = services.request_otp(phone)
        except OtpError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        # Deliver the code via the configured SMS provider (console in dev).
        message = (
            f"Your SokoPay code is {code}. It expires in 5 minutes. "
            "Never share it, not even with SokoPay staff."
        )
        result = get_sms_provider().send(phone, message)
        if not result.success:
            logger.error("Failed to send OTP SMS to %s: %s", phone, result.message)
            return Response(
                {"error": "Could not send the code right now. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"sent": True})


class OtpVerifyView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        form = OtpVerifySerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            user, tokens, is_new = services.verify_otp_and_login(
                form.validated_data["phone"], form.validated_data["code"]
            )
        except (services.AuthError, OtpError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if tokens is None:
            # Existing customer with a PIN: the SMS code alone isn't enough — ask for the PIN.
            return Response({"next": "pin_login", "tokens": None, "is_new": False, "needs_pin": False})
        return Response({
            "next": "set_pin" if not user.has_pin else "home",
            "tokens": tokens,
            "is_new": is_new,
            "needs_pin": not user.has_pin,
            "user": MeSerializer(_me(user)).data,
        })


class PinSetView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = PinSetSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            services.set_pin(request.user, form.validated_data["pin"])
        except services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True})


class PinChangeView(APIView):
    """POST /auth/pin/change {current_pin, new_pin} → fresh tokens; other devices signed out."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = PinChangeSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            new_tokens = services.change_pin(request.user, form.validated_data["current_pin"],
                                             form.validated_data["new_pin"])
        except services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "tokens": new_tokens})


class PinForgotView(APIView):
    """POST /auth/pin/forgot {phone} — sends a reset code. Same answer whether or not the
    number has an account (no account enumeration)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        form = OtpRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        phone = form.validated_data["phone"]
        try:
            code = services.request_pin_reset(phone)
        except OtpError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if code:
            get_sms_provider().send(phone, f"Your SokoPay PIN reset code is {code}. It expires in 5 minutes. "
                                           "Never share it. If you didn't ask for this, ignore this message.")
        return Response({"sent": True})


class PinResetView(APIView):
    """POST /auth/pin/reset {phone, code, new_pin, ghana_card?} → tokens."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        form = PinResetSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        d = form.validated_data
        try:
            user, new_tokens = services.reset_pin(phone=d["phone"], code=d["code"], new_pin=d["new_pin"],
                                                  ghana_card=d.get("ghana_card", ""))
        except (services.AuthError, OtpError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"tokens": new_tokens, "user": MeSerializer(_me(user)).data})


class ProfileView(APIView):
    """GET / PATCH /auth/profile {full_name?, email?}"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.kyc.models import KycProfile
        locked = KycProfile.objects.filter(user=request.user, ghana_card_hash__isnull=False).exists()
        return Response({**_me(request.user), "email": request.user.email, "name_locked": locked})

    def patch(self, request):
        form = ProfileSerializer(data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        try:
            services.update_profile(request.user, **form.validated_data)
        except services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self.get(request)


class CloseAccountView(APIView):
    """POST /auth/close {pin, reason?} — close my account (records kept as the law requires)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = CloseAccountSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            services.close_account(request.user, pin=form.validated_data["pin"],
                                   reason=form.validated_data.get("reason", ""))
        except services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"closed": True})


class LogoutView(APIView):
    """POST /auth/logout {refresh?} — end this device's session (tokens revoked)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = LogoutSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        payload = request.auth if isinstance(request.auth, dict) else None
        services.logout(request.user, payload, form.validated_data.get("refresh"))
        return Response({"ok": True})


class LogoutAllView(APIView):
    """POST /auth/logout-all — sign out of every device, including this one."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        services.logout_everywhere(request.user)
        return Response({"ok": True})


class PinLoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        form = PinLoginSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            tokens = services.login_with_pin(
                form.validated_data["phone"], form.validated_data["pin"]
            )
        except services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({"tokens": tokens})


class RefreshView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        form = RefreshSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            tokens = services.refresh(form.validated_data["refresh"])
        except services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({"tokens": tokens})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(_me(request.user)).data)


def _me(user) -> dict:
    return {
        "phone": user.phone,
        "full_name": user.full_name,
        "has_pin": user.has_pin,
        "user_type": user.user_type,
    }
