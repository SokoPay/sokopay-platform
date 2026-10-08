"""
Saved recipients (favourites) and recently paid merchants.

  GET    /wallet/saved?kind=merchant|sokopay|momo|bank|wallet
  POST   /wallet/saved        {kind, value, institution?, label?}
  DELETE /wallet/saved/<id>
  GET    /wallet/merchants/recent          last merchant payments (newest first)

A recipient is saved only after the same check a payment would do (the SokoPay
account exists, the merchant is live, the bank/MoMo name enquiry answers), so a typo is
never stored. The stored label is what the customer sees; sending still re-checks the
destination every time.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money

from .models import SavedRecipient

MAX_SAVED = 50


class SavedError(ValueError):
    pass


def _json(r: SavedRecipient) -> dict:
    return {"id": r.id, "kind": r.kind, "kind_display": r.get_kind_display(), "label": r.label,
            "value": r.value, "institution": r.institution,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None}


def save(*, owner, kind: str, value: str, institution: str = "", label: str = "") -> SavedRecipient:
    if kind not in SavedRecipient.Kind.values:
        raise SavedError("Unknown recipient type.")
    if SavedRecipient.objects.filter(owner=owner).count() >= MAX_SAVED:
        raise SavedError(f"You can save up to {MAX_SAVED} recipients. Remove one first.")
    value, institution, label = (value or "").strip(), (institution or "").strip().lower(), (label or "").strip()

    if kind == SavedRecipient.Kind.MERCHANT:
        from apps.merchants.models import Merchant
        m = Merchant.objects.filter(short_code=value.upper()).first()
        if m is None or not m.is_live:
            raise SavedError("That merchant code isn't recognised.")
        value, institution, label = m.short_code, "", label or (m.trading_name or m.legal_name)
    elif kind == SavedRecipient.Kind.SOKOPAY:
        from . import services
        from .accounts_lookup import AccountNotFound, resolve_account, wallet_number_for
        try:
            user = resolve_account(value)
        except AccountNotFound as exc:
            raise SavedError(str(exc)) from exc
        if user.id == owner.id:
            raise SavedError("That's your own account.")
        value, institution = wallet_number_for(user), ""
        label = label or services.display_name(user)
    else:
        from apps.connectors.types import DestinationType, TransferDestination

        from . import interop
        from .serializers import DestinationSerializer
        form = DestinationSerializer(data={"destination_type": kind, "institution": institution, "account": value})
        if not form.is_valid():
            raise SavedError("Check the network or bank and the account number.")
        result = interop.name_enquiry(TransferDestination(type=DestinationType(kind), institution=institution,
                                                          account=value))
        if not result.found:
            raise SavedError(result.message or "We couldn't confirm that account.")
        label = label or result.account_name
    try:
        r, created = SavedRecipient.objects.get_or_create(owner=owner, kind=kind, value=value, institution=institution,
                                                          defaults={"label": label[:80]})
    except IntegrityError:
        r, created = SavedRecipient.objects.get(owner=owner, kind=kind, value=value, institution=institution), False
    if not created and label and r.label != label[:80]:
        r.label = label[:80]
        r.save(update_fields=["label"])
    return r


def touch(owner, kind: str, value: str, institution: str = "") -> None:
    """Mark a saved recipient as just used (sorts it to the top)."""
    SavedRecipient.objects.filter(owner=owner, kind=kind, value=value, institution=institution) \
        .update(last_used_at=timezone.now())


class _SaveSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=SavedRecipient.Kind.choices)
    value = serializers.CharField(max_length=34)
    institution = serializers.CharField(max_length=32, required=False, allow_blank=True)
    label = serializers.CharField(max_length=80, required=False, allow_blank=True)


class SavedRecipientsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = SavedRecipient.objects.filter(owner=request.user)
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        qs = qs.order_by("-last_used_at", "-created_at")
        return Response({"results": [_json(r) for r in qs]})

    def post(self, request):
        form = _SaveSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            r = save(owner=request.user, **form.validated_data)
        except SavedError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_json(r), status=status.HTTP_201_CREATED)


class SavedRecipientDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        deleted, _ = SavedRecipient.objects.filter(owner=request.user, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT if deleted else status.HTTP_404_NOT_FOUND)


class RecentMerchantsView(APIView):
    """Merchant payments the customer made from their wallet or by MoMo, newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.payments.models import Payment
        saved = set(SavedRecipient.objects.filter(owner=request.user, kind="merchant").values_list("value", flat=True))
        qs = (Payment.objects.filter(user=request.user, purpose=Payment.Purpose.MERCHANT,
                                     status__in=("succeeded", "refunded"))
              .select_related("merchant").order_by("-created_at")[:30])
        return Response({"results": [{
            "reference": p.reference,
            "merchant_name": p.merchant.trading_name or p.merchant.legal_name,
            "merchant_code": p.merchant.short_code or "",
            "amount_minor": p.amount_minor,
            "amount_display": Money(p.amount_minor, p.currency).format(),
            "status": p.status,
            "created_at": p.created_at.isoformat(),
            "saved": (p.merchant.short_code or "") in saved,
        } for p in qs]})
