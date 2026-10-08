"""
Marketplace API (JWT). PSP Enhanced feature; a blocked capability returns 403 via the
project exception handler.

  GET  /marketplace/products?category=insurance|lending
  POST /marketplace/apply   {product_code, amount?, consent}
  GET  /marketplace/applications
"""

from __future__ import annotations

from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money

from . import services
from .models import FinancialProduct, ProductApplication


def _money(minor):
    return None if minor is None else Money(minor, "GHS").format()


class ApplySerializer(serializers.Serializer):
    product_code = serializers.CharField(max_length=64)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2,
                                      min_value=Decimal("0.01"), required=False)
    consent = serializers.BooleanField()


class ProductListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        products = services.list_products(request.query_params.get("category"))
        return Response([{
            "code": p.code, "name": p.name, "category": p.category, "summary": p.summary,
            "provider": p.provider.name, "regulator": p.provider.regulator,
            "min": _money(p.min_minor), "max": _money(p.max_minor),
        } for p in products])


class ApplyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = ApplySerializer(data=request.data)
        form.is_valid(raise_exception=True)
        d = form.validated_data
        product = get_object_or_404(FinancialProduct, code=d["product_code"])
        amount = Money.from_major(d["amount"], "GHS").minor if d.get("amount") else None
        try:
            app = services.apply(user=request.user, product=product,
                                 amount_minor=amount, consent=d["consent"])
        except services.MarketplaceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"reference": app.reference, "status": app.status,
                         "message": app.message}, status=status.HTTP_201_CREATED)


class ApplicationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        apps = ProductApplication.objects.filter(user=request.user) \
            .select_related("product__provider").order_by("-created_at")[:50]
        return Response([{
            "reference": a.reference, "product": a.product.name,
            "provider": a.product.provider.name, "category": a.product.category,
            "amount": _money(a.amount_minor), "status": a.status, "created_at": a.created_at,
            # Savings / investment / pension: what has moved through SokoPay so far.
            **({"position": services.position(a)} if a.product.category in services.SAVINGS_LIKE else {}),
        } for a in apps])


class _PremiumSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class PayPremiumView(APIView):
    """POST /marketplace/applications/<reference>/pay-premium {amount} — from the wallet (DEMI)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, reference):
        application = get_object_or_404(ProductApplication, reference=reference,
                                        user=request.user)
        form = _PremiumSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            txn = services.pay_premium(
                user=request.user, application=application,
                amount_minor=Money.from_major(form.validated_data["amount"], "GHS").minor,
            )
        except services.MarketplaceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"kind": txn.kind, "amount": _money(txn.amount_minor)},
                        status=status.HTTP_201_CREATED)


class _MoveSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    pin = serializers.RegexField(r"^\d{6}$")


class _SavingsMoveView(APIView):
    """POST /marketplace/applications/<reference>/contribute|withdraw {amount, pin}"""

    permission_classes = [IsAuthenticated]
    action = "contribute"

    def post(self, request, reference):
        application = get_object_or_404(ProductApplication.objects.select_related("product__provider"),
                                        reference=reference, user=request.user)
        form = _MoveSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        fn = services.contribute if self.action == "contribute" else services.request_withdrawal
        try:
            txn = fn(user=request.user, application=application, pin=form.validated_data["pin"],
                     amount_minor=Money.from_major(form.validated_data["amount"], "GHS").minor)
        except services.MarketplaceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"kind": txn.kind, "status": txn.status, "amount": _money(txn.amount_minor),
                         "position": services.position(application)}, status=status.HTTP_201_CREATED)


class ContributeView(_SavingsMoveView):
    action = "contribute"


class WithdrawView(_SavingsMoveView):
    action = "withdraw"
