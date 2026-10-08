"""DRF serializers for the payments API."""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.common.money import Money

from .models import Biller, Payment


class BillerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Biller
        fields = ("code", "name", "category", "network", "supports_lookup")


class PaymentSerializer(serializers.ModelSerializer):
    """Read view of a payment. Amounts are shown in major units; payer is masked."""

    amount = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    payer = serializers.CharField(source="payer_masked", read_only=True)
    biller = serializers.CharField(source="biller.name", default="", read_only=True)

    class Meta:
        model = Payment
        fields = (
            "reference", "purpose", "status", "amount", "fee", "total", "currency",
            "funding_source", "network", "payer", "biller", "account_ref", "product_code",
            "delivery_token", "failure_code", "created_at", "completed_at",
        )

    def _major(self, minor: int, currency: str) -> str:
        return f"{Money(minor, currency).major}"

    def get_amount(self, obj):
        return self._major(obj.amount_minor, obj.currency)

    def get_fee(self, obj):
        return self._major(obj.fee_minor, obj.currency)

    def get_total(self, obj):
        return self._major(obj.total_minor, obj.currency)


class _FundingSource(serializers.Serializer):
    """
    How the customer pays: "momo" (approve a mobile-money prompt — needs `network` and
    `payer`) or "wallet" (debit their SokoPay wallet; DEMI licence).
    """

    source = serializers.ChoiceField(choices=["momo", "wallet"], default="momo")
    network = serializers.ChoiceField(choices=["mtn", "telecel", "at"], required=False)
    payer = serializers.RegexField(r"^\+233\d{9}$", required=False)  # E.164 Ghana

    def validate(self, data):
        if data.get("source", "momo") == "momo" and not (data.get("network") and data.get("payer")):
            raise serializers.ValidationError(
                "Choose a mobile money network and number to pay from."
            )
        return data


class _BasePaymentRequest(_FundingSource):
    """Shared input validation for initiating a payment."""

    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))

    def amount_minor(self) -> int:
        # Convert via the Money type so rounding is consistent and float-free.
        return Money.from_major(self.validated_data["amount"], "GHS").minor


class BillPaymentRequest(_BasePaymentRequest):
    biller_code = serializers.CharField()
    account = serializers.CharField(max_length=64)   # meter / smartcard / account number


class AirtimeRequest(_BasePaymentRequest):
    biller_code = serializers.CharField()
    phone = serializers.RegexField(r"^\+233\d{9}$")  # number being topped up


class DataBundleRequest(_FundingSource):
    """Buy a bundle. No amount field: the price always comes from the catalogue."""

    telco = serializers.ChoiceField(choices=["mtn", "telecel", "at"])   # bundle's network
    phone = serializers.RegexField(r"^\+233\d{9}$")                     # number receiving data
    bundle_code = serializers.CharField(max_length=64)
    # `network` (inherited) is the payer's MoMo network, which may differ from `telco`.
