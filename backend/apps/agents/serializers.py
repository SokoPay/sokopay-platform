from decimal import Decimal

from rest_framework import serializers


class CashOpSerializer(serializers.Serializer):
    """`customer` is what the customer tells the agent: phone number or SokoPay wallet ID.
    (`customer_phone` is still accepted from older app builds.)"""

    customer = serializers.CharField(max_length=20, required=False)
    customer_phone = serializers.CharField(max_length=20, required=False)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )

    def validate(self, data):
        ident = (data.get("customer") or data.get("customer_phone") or "").strip()
        if not ident:
            raise serializers.ValidationError({"customer": "Enter the customer's phone or wallet ID."})
        data["customer"] = ident
        return data


class TxnSerializer(serializers.Serializer):
    kind = serializers.CharField()
    customer_phone = serializers.CharField()
    amount_minor = serializers.IntegerField()
    created_at = serializers.DateTimeField()
