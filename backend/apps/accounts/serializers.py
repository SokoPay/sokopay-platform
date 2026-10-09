from rest_framework import serializers

PHONE = r"^\+233\d{9}$"


class OtpRequestSerializer(serializers.Serializer):
    phone = serializers.RegexField(PHONE)


class OtpVerifySerializer(serializers.Serializer):
    phone = serializers.RegexField(PHONE)
    code = serializers.RegexField(r"^\d{6}$")


class PinSetSerializer(serializers.Serializer):
    pin = serializers.RegexField(r"^\d{6}$")


class PinLoginSerializer(serializers.Serializer):
    phone = serializers.RegexField(PHONE)
    pin = serializers.RegexField(r"^\d{6}$")


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(max_length=2048)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(max_length=2048, required=False, allow_blank=True)


class MeSerializer(serializers.Serializer):
    phone = serializers.CharField()
    full_name = serializers.CharField()
    has_pin = serializers.BooleanField()
    user_type = serializers.CharField()


class PinChangeSerializer(serializers.Serializer):
    current_pin = serializers.RegexField(r"^\d{6}$")
    new_pin = serializers.RegexField(r"^\d{6}$")


class PinResetSerializer(serializers.Serializer):
    phone = serializers.RegexField(PHONE)
    code = serializers.RegexField(r"^\d{6}$")
    new_pin = serializers.RegexField(r"^\d{6}$")
    ghana_card = serializers.CharField(max_length=20, required=False, allow_blank=True)


class ProfileSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150, required=False)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    gps_address = serializers.CharField(max_length=20, required=False, allow_blank=True)


class CloseAccountSerializer(serializers.Serializer):
    pin = serializers.RegexField(r"^\d{6}$")
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
