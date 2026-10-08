"""Project-wide DRF exception handling."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.kyc.exceptions import KycError
from apps.licensing.exceptions import CapabilityNotLicensed


def sokopay_exception_handler(exc, context):
    """
    Map domain exceptions to clean HTTP responses.

    * CapabilityNotLicensed — built, but the current BoG licence doesn't permit it (403).
    * KycError — a wallet limit, compliance hold or failed verification (400). Its
      message is written for the customer, so it is passed through as-is.
    """
    if isinstance(exc, CapabilityNotLicensed):
        return Response(
            {"error": "not_licensed",
             "detail": "This action is not permitted under the current licence."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, KycError):
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return exception_handler(exc, context)
