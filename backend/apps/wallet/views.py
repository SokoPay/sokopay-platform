"""
Consumer wallet API (JWT-authenticated). DEMI feature; money operations are gated in
the service layer, and a blocked capability becomes a 403 via the project handler.

  GET  /wallet            → balance + recent activity
  POST /wallet/fund       {amount, network, payer?}   → start a MoMo top-up
  POST /wallet/send       {recipient_phone, amount}   → P2P transfer
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money
from apps.connectors.types import DestinationType, TransferDestination
from apps.merchants import qr
from apps.merchants.exceptions import MerchantError
from apps.payments.serializers import PaymentSerializer
from apps.rails.types import Network

from .accounts_lookup import AccountNotFound, format_wallet_number, resolve_account, wallet_number_for
from . import interop, merchant_pay, services
from . import remittance as remittance_service
from .exceptions import WalletError
from .models import ExternalTransfer
from .serializers import (
    DestinationSerializer,
    ExternalTransferSerializer,
    FundSerializer,
    SendSerializer,
)


def _destination(data) -> TransferDestination:
    return TransferDestination(
        type=DestinationType(data["destination_type"]),
        institution=data["institution"],
        account=data["account"],
    )


def _transfer_json(t: ExternalTransfer) -> dict:
    return {
        "reference": t.reference,
        "destination_type": t.destination_type,
        "institution": t.institution,
        "account": t.account,
        "account_name": t.account_name,
        "amount": Money(t.amount_minor, t.currency).format(),
        "status": t.status,
        "failure_code": t.failure_code,
        "created_at": t.created_at,
    }


class WalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bal = services.balance(request.user)
        activity = [
            {
                "direction": row["direction"],
                "amount": Money(row["amount_minor"], "GHS").format(),
                "narrative": row["narrative"],
                "at": row["at"],
            }
            for row in services.history(request.user)
        ]
        number = wallet_number_for(request.user)
        return Response({
            "wallet_number": number,
            "wallet_number_display": format_wallet_number(number),
            "balance_minor": bal,
            "balance_display": Money(bal, "GHS").format(),
            "activity": activity,
        })


class WalletFundView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = FundSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            payment = services.initiate_funding(
                user=request.user,
                amount_minor=Money.from_major(form.validated_data["amount"], "GHS").minor,
                network=Network(form.validated_data["network"]),
                payer=form.validated_data.get("payer"),
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except WalletError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class WalletSendLookupView(APIView):
    """GET /wallet/send/lookup?account=<phone or wallet ID> (or ?phone=) — is this a
    SokoPay user, and roughly who?"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        ident = (request.query_params.get("account") or request.query_params.get("phone") or "").strip()
        try:
            user = resolve_account(ident)
        except AccountNotFound as exc:
            return Response({"found": False, "name": "", "message": str(exc)})
        if user.id == request.user.id:
            return Response({"found": False, "name": "", "self": True})
        return Response({"found": True, "name": services.display_name(user)})


class WalletSendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = SendSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        ident = form.validated_data.get("recipient") or form.validated_data.get("recipient_phone") or ""
        try:
            recipient_phone = resolve_account(ident).phone
        except AccountNotFound as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = services.send_p2p(
                sender=request.user,
                recipient_phone=recipient_phone,
                amount_minor=Money.from_major(form.validated_data["amount"], "GHS").minor,
            )
        except WalletError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "recipient": result["recipient"],
            "recipient_name": result["recipient_name"],
            "amount": Money(result["amount_minor"], "GHS").format(),
            "new_balance": Money(result["new_balance_minor"], "GHS").format(),
        })


class PayResolveView(APIView):
    """GET /pay/resolve?code=… — what a scanned/typed code means, before paying."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            target = qr.resolve(request.query_params.get("code", ""))
        except MerchantError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        amt = target["amount_minor"]
        return Response({**target, "amount": None if amt is None else f"{Money(amt, 'GHS').major}"})


class _PayMerchantSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=200)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2,
                                      min_value=Decimal("0.01"), required=False)


class PayMerchantView(APIView):
    """POST /wallet/pay-merchant {code, amount?} — pay a merchant from the wallet (DEMI)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = _PayMerchantSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        amount = form.validated_data.get("amount")
        try:
            payment = merchant_pay.pay(
                user=request.user,
                code=form.validated_data["code"],
                amount_minor=Money.from_major(amount, "GHS").minor if amount is not None else None,
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except WalletError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = PaymentSerializer(payment).data
        data["merchant"] = payment.merchant.trading_name or payment.merchant.legal_name
        return Response(data, status=status.HTTP_201_CREATED)


class RemittanceWebhookView(APIView):
    """
    POST /remittance/<partner>/webhook — a licensed remittance partner notifies us of a
    transfer to credit. Open endpoint; security is the partner's signature.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    MAX_BODY = 64 * 1024

    def post(self, request, partner):
        from apps.connectors import registry
        from apps.connectors.placeholder import ConnectorNotImplemented

        if len(request.body) > self.MAX_BODY:
            return Response({"error": "payload too large"},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        try:
            # Mock partners are unknown unless ALLOW_MOCK_INTEGRATIONS (never in prod).
            connector = registry.remittance_connector(partner)
        except registry.UnknownConnector:
            return Response({"error": "unknown partner"}, status=status.HTTP_404_NOT_FOUND)
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            event = connector.verify_and_parse(headers, request.body)
        except ConnectorNotImplemented:
            return Response({"error": "partner not live"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not event.signature_ok:
            return Response({"status": "rejected"}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            remittance = remittance_service.credit_inbound(partner, event)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": remittance.status, "reason": remittance.reason})


class TransferLookupView(APIView):
    """POST /wallet/transfer/lookup — confirm the recipient's name before sending."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = DestinationSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        result = interop.name_enquiry(_destination(form.validated_data))
        return Response({
            "found": result.found, "account_name": result.account_name,
            "supported": result.supported, "message": result.message,
        })


class ExternalTransferView(APIView):
    """POST /wallet/transfer — send to another MoMo wallet, bank or fintech.
    GET  /wallet/transfer — the user's recent external transfers."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = ExternalTransfer.objects.filter(sender=request.user).order_by("-created_at")[:50]
        return Response([_transfer_json(t) for t in rows])

    def post(self, request):
        form = ExternalTransferSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        d = form.validated_data
        try:
            transfer = interop.send_external(
                sender=request.user,
                destination=_destination(d),
                amount_minor=Money.from_major(d["amount"], "GHS").minor,
                narrative=d.get("narrative", ""),
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except WalletError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_transfer_json(transfer), status=status.HTTP_201_CREATED)
