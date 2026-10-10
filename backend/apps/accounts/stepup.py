"""
Step-up authentication: the customer's PIN, checked on the server, before money leaves a
wallet (send to a SokoPay user, transfer out, pay a merchant, bills/airtime/data paid
from the wallet). A signed-in session alone is never enough to move money: a stolen or
borrowed phone with an open session still can't pay without the PIN.

Wrong PINs share the sign-in lockout counter (5 wrong → locked for a while), so guesses
can't be spread between the login screen and payment prompts.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from . import services

PIN_REQUIRED = "Enter your PIN to confirm this payment."


def require_pin(request) -> Response | None:
    """None if the request carries the user's correct PIN; otherwise the error response to return."""
    pin = request.data.get("pin") if hasattr(request.data, "get") else None
    pin = str(pin or "")
    if not pin:
        return Response({"error": PIN_REQUIRED, "pin_required": True}, status=status.HTTP_400_BAD_REQUEST)
    try:
        services.confirm_pin(request.user, pin)
    except services.AuthError as exc:
        return Response({"error": str(exc), "pin_required": True}, status=status.HTTP_403_FORBIDDEN)
    return None
