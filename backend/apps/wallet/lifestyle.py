"""
Tickets and food: buy from a lifestyle partner, pay from the SokoPay wallet.

  GET  /lifestyle/<category>             partner + offerings (available: false until a partner is live)
  POST /lifestyle/orders                 {category, offering_code, quantity, pin}
  GET  /lifestyle/orders                 my orders (ticket codes included)

The price always comes from the partner's own list on the server, never from the app.
Money (reserve → partner → resolve):
  reserve:   Dr customer_wallet       Cr lifestyle_in_flight
  confirmed: Dr lifestyle_in_flight   Cr lifestyle_partner_payable:<partner>
  refused:   Dr lifestyle_in_flight   Cr customer_wallet   (refund)
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.connectors.registry import UnknownConnector, get_connector
from apps.connectors.types import PurchaseRequest
from apps.common.money import Money
from apps.kyc import limits as kyc_limits
from apps.kyc.exceptions import KycError
from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.rails.types import RailStatus

from .exceptions import WalletError
from .models import LifestyleOrder

logger = logging.getLogger("sokopay.lifestyle")
CATEGORIES = ("ticketing", "food")
MAX_QUANTITY = 10
_REF = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class LifestyleError(WalletError):
    pass


def _partner(category: str):
    key = (getattr(settings, "LIFESTYLE_PARTNERS", {}) or {}).get(category, "")
    if category not in CATEGORIES or not key:
        raise LifestyleError("This isn't available yet.")
    try:
        c = get_connector("lifestyle", key)
    except UnknownConnector:
        raise LifestyleError("This isn't available yet.") from None
    if not getattr(c, "available", False):
        raise LifestyleError("This isn't available yet.")
    return c


def catalogue(category: str) -> dict:
    try:
        c = _partner(category)
    except LifestyleError as exc:
        return {"available": False, "message": str(exc), "offerings": []}
    return {"available": True, "partner": c.display_name, "offerings": [
        {"code": o.code, "name": o.name, "description": o.description, "price_minor": o.price_minor,
         "price_display": ghs(o.price_minor)} for o in c.list_offerings()]}


@kyc_limits.debits_serialized("user")
def order(*, user, category: str, offering_code: str, quantity: int, pin: str) -> LifestyleOrder:
    from apps.accounts import services as auth_services
    try:
        auth_services.confirm_pin(user, pin)
    except auth_services.AuthError as exc:
        raise LifestyleError(str(exc)) from exc
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    c = _partner(category)
    if not 1 <= quantity <= MAX_QUANTITY:
        raise LifestyleError(f"Choose between 1 and {MAX_QUANTITY}.")
    offering = next((o for o in c.list_offerings() if o.code == offering_code), None)
    if offering is None:
        raise LifestyleError("That item isn't available any more.")
    total = offering.price_minor * quantity
    kyc_limits.check_debit(user, total)
    with transaction.atomic():
        o = LifestyleOrder.objects.create(
            reference="LS-" + "".join(secrets.choice(_REF) for _ in range(10)), user=user, category=category,
            partner=c.key, offering_code=offering.code, offering_name=offering.name[:150], quantity=quantity,
            unit_price_minor=offering.price_minor, total_minor=total)
        try:
            post_entry(f"{category.title()} order {o.reference}",
                       [debit(accounts.customer_wallet(str(user.id)), total), credit(accounts.lifestyle_in_flight(), total)],
                       idempotency_key=f"ls-reserve:{o.id}", reference=("lifestyle_order", str(o.id)))
        except InsufficientFunds as exc:
            raise LifestyleError("Insufficient wallet balance.") from exc
    try:
        result = c.purchase(PurchaseRequest(offering_code=offering.code, quantity=quantity, buyer_phone=user.phone,
                                            amount=Money(total, "GHS"), reference=o.reference,
                                            idempotency_key=f"ls-buy:{o.id}"))
    except Exception as exc:   # noqa: BLE001 - unknown outcome: wait for the poller, never refund blindly
        logger.error("Lifestyle order %s purchase raised: %s", o.reference, exc)
        return o
    LifestyleOrder.objects.filter(pk=o.pk).update(provider_ref=(result.provider_ref or "")[:64],
                                                  token=(result.token or "")[:128])
    o.refresh_from_db()
    return apply_outcome(o, result.status, result.failure_code)


def apply_outcome(o: LifestyleOrder, st: RailStatus, failure_code: str = "") -> LifestyleOrder:
    with transaction.atomic():
        o = LifestyleOrder.objects.select_for_update().get(pk=o.pk)
        if o.status != LifestyleOrder.Status.PENDING:
            return o
        in_flight = accounts.lifestyle_in_flight()
        if st == RailStatus.SUCCEEDED:
            post_entry(f"{o.category.title()} order {o.reference} confirmed",
                       [debit(in_flight, o.total_minor), credit(accounts.lifestyle_partner_payable(o.partner), o.total_minor)],
                       idempotency_key=f"ls-settle:{o.id}", reference=("lifestyle_order", str(o.id)))
            o.status = LifestyleOrder.Status.SUCCEEDED
            body = (f"{o.quantity} × {o.offering_name}. Ticket code {o.token}." if o.token
                    else f"{o.quantity} × {o.offering_name} confirmed.")
            title = "Order confirmed"
        elif st == RailStatus.FAILED:
            post_entry(f"{o.category.title()} order {o.reference} refused, refund",
                       [debit(in_flight, o.total_minor), credit(accounts.customer_wallet(str(o.user_id)), o.total_minor)],
                       idempotency_key=f"ls-refund:{o.id}", reference=("lifestyle_order", str(o.id)))
            o.status, o.failure_code = LifestyleOrder.Status.FAILED, (failure_code or "declined")[:64]
            title, body = "Order refunded", f"{o.offering_name} couldn't be confirmed. {ghs(o.total_minor)} is back in your wallet."
        else:
            return o
        o.completed_at = timezone.now()
        o.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])
        notify(o.user, kind="wallet", title=title, body=body, app="customer", data={"type": "wallet"})
    return o


def resolve_pending(limit: int = 300) -> dict:
    n = 0
    for o in LifestyleOrder.objects.filter(status="pending").exclude(provider_ref="").order_by("updated_at")[:limit]:
        try:
            st = get_connector("lifestyle", o.partner).get_status(o.provider_ref)
        except Exception as exc:   # noqa: BLE001
            logger.warning("Lifestyle %s status check failed: %s", o.reference, exc)
            continue
        if apply_outcome(o, st.status, st.failure_code).status != "pending":
            n += 1
    return {"resolved": n}


def order_json(o: LifestyleOrder) -> dict:
    return {"reference": o.reference, "category": o.category, "name": o.offering_name, "quantity": o.quantity,
            "total_display": ghs(o.total_minor), "status": o.status, "status_display": o.get_status_display(),
            "ticket_code": o.token, "created_at": o.created_at.isoformat()}


# --- API ------------------------------------------------------------------------------------
class CatalogueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, category):
        return Response(catalogue(category))


class _OrderSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=CATEGORIES)
    offering_code = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY)
    pin = serializers.RegexField(r"^\d{6}$")


class OrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = LifestyleOrder.objects.filter(user=request.user).order_by("-created_at")[:50]
        return Response({"results": [order_json(o) for o in qs]})

    def post(self, request):
        form = _OrderSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            o = order(user=request.user, **form.validated_data)
        except (LifestyleError, KycError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(order_json(o), status=status.HTTP_201_CREATED)
