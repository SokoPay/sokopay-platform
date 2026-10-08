from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.common.money import Money


class MerchantChargeRequest(serializers.Serializer):
    """Input for creating a merchant collection via the merchant API."""

    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    network = serializers.ChoiceField(choices=["mtn", "telecel", "at"])
    payer = serializers.RegexField(r"^\+233\d{9}$")
    narrative = serializers.CharField(required=False, allow_blank=True, max_length=120)

    def amount_minor(self) -> int:
        return Money.from_major(self.validated_data["amount"], "GHS").minor
