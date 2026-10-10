"""
Payments API.

Thin HTTP layer over apps.payments.services — the views validate input and translate
service results to responses. All the money logic (and its tests) lives in services.

The consumer endpoints require authentication. The webhook endpoint is open but
signature-verified inside the service; it never trusts the request on face value.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.stepup import require_pin

from apps.common.money import Money
from apps.rails.exceptions import WebhookVerificationError
from apps.rails.types import Network

from . import services
from .models import Biller, Payment
from .serializers import (
    AirtimeRequest,
    BillerSerializer,
    BillPaymentRequest,
    DataBundleRequest,
    PaymentSerializer,
)


def _idempotency_key(request) -> str | None:
    """Clients send an Idempotency-Key header on money-moving POSTs."""
    return request.headers.get("Idempotency-Key")


def _wallet_pin(request, data):
    """Paying from the SokoPay wallet needs the PIN (MoMo-funded payments are approved on
    the phone with the MoMo PIN instead)."""
    if data.get("source", "momo") == "wallet":
        return require_pin(request)
    return None


def _funding(data) -> dict:
    """source / network / payer kwargs for the payment services."""
    network = data.get("network")
    return {
        "source": data.get("source", "momo"),
        "network": Network(network) if network else None,
        "payer": data.get("payer"),
    }


class BillerListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        billers = Biller.objects.filter(is_active=True)
        category = request.query_params.get("category")
        if category:
            billers = billers.filter(category=category)
        return Response(BillerSerializer(billers, many=True).data)


class BillPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = BillPaymentRequest(data=request.data)
        form.is_valid(raise_exception=True)
        if (denied := _wallet_pin(request, form.validated_data)) is not None:
            return denied
        biller = get_object_or_404(Biller, code=form.validated_data["biller_code"], is_active=True)
        try:
            payment = services.initiate_bill_payment(
                user=request.user,
                biller=biller,
                account_ref=form.validated_data["account"],
                amount_minor=form.amount_minor(),
                idempotency_key=_idempotency_key(request),
                **_funding(form.validated_data),
            )
        except services.PaymentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class AirtimeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = AirtimeRequest(data=request.data)
        form.is_valid(raise_exception=True)
        if (denied := _wallet_pin(request, form.validated_data)) is not None:
            return denied
        biller = get_object_or_404(Biller, code=form.validated_data["biller_code"], is_active=True)
        try:
            payment = services.initiate_airtime(
                user=request.user,
                biller=biller,
                phone=form.validated_data["phone"],
                amount_minor=form.amount_minor(),
                idempotency_key=_idempotency_key(request),
                **_funding(form.validated_data),
            )
        except services.PaymentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class BillerLookupView(APIView):
    """GET /billers/<code>/lookup?account=… — confirm whose account it is before paying."""

    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        biller = get_object_or_404(Biller, code=code, is_active=True)
        account = request.query_params.get("account", "").strip()
        if not account:
            return Response({"error": "account is required"}, status=status.HTTP_400_BAD_REQUEST)
        result = services.lookup_account(biller, account)
        return Response({
            "found": result.found,
            "account_name": result.account_name,
            "outstanding_minor": result.outstanding_minor,
            "supported": result.supported,
            "message": result.message,
        })


class BundleListView(APIView):
    """GET /telcos/<telco>/bundles — data bundles on sale for MTN, Telecel or AT."""

    permission_classes = [IsAuthenticated]

    def get(self, request, telco):
        if telco not in ("mtn", "telecel", "at"):
            return Response({"error": "Unknown network."}, status=status.HTTP_404_NOT_FOUND)
        try:
            bundles = services.list_bundles(telco)
        except services.PaymentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response([{
            "code": b.code, "name": b.name, "network": b.network,
            "price": f"{Money(b.price_minor, 'GHS').major}", "price_minor": b.price_minor,
            "volume": b.volume, "validity": b.validity, "is_sample": b.is_sample,
        } for b in bundles])


class DataBundleView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = DataBundleRequest(data=request.data)
        form.is_valid(raise_exception=True)
        if (denied := _wallet_pin(request, form.validated_data)) is not None:
            return denied
        d = form.validated_data
        try:
            funding = _funding(d)
            payment = services.initiate_data_bundle(
                user=request.user, telco=d["telco"], phone=d["phone"],
                bundle_code=d["bundle_code"], pay_network=funding["network"],
                payer=funding["payer"], source=funding["source"],
                idempotency_key=_idempotency_key(request),
            )
        except services.PaymentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class PaymentStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        payment = get_object_or_404(Payment, reference=reference, user=request.user)
        body = PaymentSerializer(payment).data
        if payment.purpose == Payment.Purpose.MERCHANT:
            from apps.merchants.dispute_views import customer_dispute_summary
            body.update(customer_dispute_summary(payment))
        return Response(body)


class RailWebhookView(APIView):
    """Inbound callbacks from a rail. Open endpoint; security is signature + re-query."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    MAX_BODY = 64 * 1024   # real rail callbacks are a few hundred bytes

    def post(self, request, provider):
        # Only the rail this deployment is actually wired to has a callback URL. Any
        # other name is a probe (or a misconfiguration) and gets a plain 404.
        if provider.lower() != str(getattr(settings, "RAIL_PROVIDER", "")).lower():
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if len(request.body) > self.MAX_BODY:
            return Response({"error": "payload too large"},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        try:
            services.handle_webhook(provider.lower(), dict(request.headers), request.body)
        except WebhookVerificationError:
            # Do not reveal why; just reject. The event is already stored for audit.
            return Response({"status": "rejected"}, status=status.HTTP_401_UNAUTHORIZED)
        # Always 200 on an accepted (verified) callback so the rail stops retrying.
        return Response({"status": "ok"})
