import re
from decimal import Decimal

from rest_framework import serializers


class FundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    network = serializers.ChoiceField(choices=["mtn", "telecel", "at"])
    payer = serializers.RegexField(r"^\+233\d{9}$", required=False)


class SendSerializer(serializers.Serializer):
    """`recipient`: a phone number or SokoPay wallet ID (`recipient_phone` still accepted)."""

    recipient = serializers.CharField(max_length=20, required=False)
    recipient_phone = serializers.CharField(max_length=20, required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class DestinationSerializer(serializers.Serializer):
    """Where an external transfer goes: a MoMo wallet, a bank account, or a fintech wallet."""

    destination_type = serializers.ChoiceField(choices=["momo", "bank", "wallet"])
    institution = serializers.CharField(max_length=32)   # mtn | <bank code> | zeepay …
    account = serializers.CharField(max_length=34)       # phone or account number

    def validate(self, data):
        if data["destination_type"] == "momo":
            if data["institution"] not in ("mtn", "telecel", "at"):
                raise serializers.ValidationError("MoMo network must be mtn, telecel or at.")
            if not re.fullmatch(r"\+233\d{9}", data["account"]):
                raise serializers.ValidationError("MoMo account must be a +233 phone number.")
        return data


class ExternalTransferSerializer(DestinationSerializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    narrative = serializers.CharField(max_length=140, required=False, allow_blank=True)
