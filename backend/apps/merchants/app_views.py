"""
Merchant mobile app API ("SokoPay Business") — for merchant TEAM MEMBERS signed in
with the normal phone + OTP + PIN JWT, not for server integrations (those use API keys,
see views.py).

  GET  /merchant-app/me
  GET  /merchant-app/qr                                   static counter QR + short code
  GET  /merchant-app/payment-requests                     recent dynamic QRs
  POST /merchant-app/payment-requests        {amount?, description?}
  GET  /merchant-app/payment-requests/<token>             poll until paid
  POST /merchant-app/payment-requests/<token>/cancel
  GET  /merchant-app/payments                             history (filters, cursor paging)
  GET  /merchant-app/payments/<reference>
  GET  /merchant-app/settlements                          balance, accounts, history
  POST /merchant-app/settlements             {amount?, account_id?, pin}
  POST /merchant-app/settlement-accounts     {kind, provider, account_no, account_name, pin}

Roles mirror the web portal: everyone can show QRs; Owner/Admin/Finance see money;
only Owner/Finance move it — and every money action needs the PIN again (step-up),
with the same lockout as sign-in. Views are thin: the logic is in qr.py / settlement.py.
"""

from __future__ import annotations

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services as auth_services
from apps.common import pagination
from apps.common.money import Money, MoneyError
from apps.portal import ratelimit

from . import qr, settlement
from .exceptions import MerchantError, SettlementError
from .models import MerchantMember, PaymentRequest, Settlement, SettlementAccount

QR_ROLES = ("owner", "admin", "finance", "cashier")
VIEW_MONEY_ROLES = ("owner", "admin", "finance")
MOVE_MONEY_ROLES = ("owner", "finance")


def _ghs(minor) -> str:
    return Money(int(minor or 0), "GHS").format()


class MerchantAppView(APIView):
    permission_classes = [IsAuthenticated]
    allowed_roles: tuple = QR_ROLES

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        member = (MerchantMember.objects.select_related("merchant")
                  .filter(user=request.user).first())
        if member is None:
            raise PermissionDenied("This account isn't linked to a business.")
        if member.role not in self.allowed_roles:
            raise PermissionDenied("Your role can't do this.")
        request.member = member
        request.merchant = member.merchant

    @staticmethod
    def _throttled(request, action: str) -> Response | None:
        if ratelimit.allow(action, str(request.user.id)):
            return None
        return Response({"error": "Too many attempts. Please wait and try again."},
                        status=status.HTTP_429_TOO_MANY_REQUESTS)

    @staticmethod
    def _step_up(request) -> Response | None:
        try:
            auth_services.confirm_pin(request.user, str(request.data.get("pin", "")))
        except auth_services.AuthError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return None


def _request_json(req: PaymentRequest) -> dict:
    qr.expire_if_due(req)
    return {
        "token": req.token,
        "payload": qr.request_payload(req),
        "amount_minor": req.amount_minor,
        "amount_display": _ghs(req.amount_minor) if req.amount_minor else None,
        "description": req.description,
        "status": req.status,
        "expires_at": req.expires_at.isoformat(),
        "created_at": req.created_at.isoformat(),
    }


class MeView(MerchantAppView):
    def get(self, request):
        m, role = request.merchant, request.member.role
        body = {
            "merchant_name": m.trading_name or m.legal_name,
            "status": m.status,
            "is_live": m.is_live,
            "role": role,
            "can_view_money": role in VIEW_MONEY_ROLES,
            "can_move_money": role in MOVE_MONEY_ROLES,
        }
        if role in VIEW_MONEY_ROLES:
            available = settlement.available_balance_minor(m)
            body.update(available_minor=available, available_display=_ghs(available))
        return Response(body)


class StaticQrView(MerchantAppView):
    def get(self, request):
        m = request.merchant
        if not m.is_live:
            return Response({"error": "Your QR is issued once your business is approved."},
                            status=status.HTTP_409_CONFLICT)
        return Response({"merchant_name": m.trading_name or m.legal_name,
                         "short_code": qr.ensure_short_code(m), "payload": qr.static_payload(m)})


class AmountField(serializers.Field):
    """A cedi amount as text ("25.00") → integer pesewas; blank → None (whole balance /
    customer enters it). Never a float."""

    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        raw = str(data if data is not None else "").strip()
        if len(raw) > 16:
            raise serializers.ValidationError("Amount is too long.")
        if not raw:
            return None
        try:
            minor = Money.from_major(raw, "GHS").minor
        except MoneyError as exc:
            raise serializers.ValidationError("Enter the amount as a number, e.g. 25.00") from exc
        if minor <= 0:
            raise serializers.ValidationError("Amount must be more than zero.")
        return minor


class CreateRequestSerializer(serializers.Serializer):
    amount = AmountField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=120)


class PaymentRequestsView(MerchantAppView):
    def get(self, request):
        recent = request.merchant.payment_requests.order_by("-created_at")[:20]
        return Response([_request_json(r) for r in recent])

    def post(self, request):
        if (resp := self._throttled(request, "qr_request")):
            return resp
        form = CreateRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            req = qr.create_payment_request(
                merchant=request.merchant, created_by=request.user,
                amount_minor=form.validated_data.get("amount"),
                description=form.validated_data.get("description", ""),
            )
        except MerchantError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_request_json(req), status=status.HTTP_201_CREATED)


class PaymentRequestDetailView(MerchantAppView):
    def get(self, request, token):
        req = get_object_or_404(PaymentRequest, token=token, merchant=request.merchant)
        return Response(_request_json(req))


class PaymentRequestCancelView(MerchantAppView):
    def post(self, request, token):
        req = get_object_or_404(PaymentRequest, token=token, merchant=request.merchant)
        qr.expire_if_due(req)
        if req.status == PaymentRequest.Status.OPEN:
            req.status = PaymentRequest.Status.CANCELLED
            req.save(update_fields=["status", "updated_at"])
        return Response(_request_json(req))


# ---------------------------------------------------------------------------
# Payments history
# ---------------------------------------------------------------------------
PAGE_SIZE = 30
STATUS_FILTERS = {
    "paid": ["succeeded"],
    "pending": ["created", "pending"],
    "failed": ["failed"],
    "refunded": ["refunded"],
}
def _method(p) -> str:
    if p.funding_source == "wallet":
        return "SokoPay wallet"
    return {"mtn": "MTN MoMo", "telecel": "Telecel Cash", "at": "AT Money",
            "card": "Card"}.get(p.network, p.network.upper() or "—")


def _payment_json(p, *, show_money: bool, request_note: str = "") -> dict:
    body = {
        "reference": p.reference,
        "status": p.status,
        "status_display": {"succeeded": "Paid", "created": "Pending"}.get(p.status, p.get_status_display()),
        "amount_minor": p.amount_minor,
        "amount_display": _ghs(p.amount_minor),
        "method": _method(p),
        "payer": p.payer_masked,          # never the full number
        "note": request_note,
        "is_test": p.mode == "test",
        "created_at": p.created_at.isoformat(),
        "completed_at": p.completed_at.isoformat() if p.completed_at else None,
    }
    if show_money:
        # Merchants absorb the fee (MDR): net is what lands in their balance.
        body.update(fee_minor=p.fee_minor, fee_display=_ghs(p.fee_minor),
                    net_minor=p.amount_minor - p.fee_minor,
                    net_display=_ghs(p.amount_minor - p.fee_minor))
    if p.status == "failed" and p.failure_code:
        body["failure"] = {
            "rail_declined": "Declined or not approved by the customer",
            "expired": "The customer didn't approve in time",
        }.get(p.failure_code, "Payment didn't go through")
    return body


def _notes_for(payments) -> dict:
    """Description of the QR request each payment came from, if any."""
    ids = [p.id for p in payments]
    return dict(PaymentRequest.objects.filter(payment_id__in=ids)
                .exclude(description="").values_list("payment_id", "description"))


class PaymentsView(MerchantAppView):
    """
    GET /merchant-app/payments?status=paid|pending|failed|refunded&period=today|7d|30d
                              &mode=test&cursor=<opaque>
    Newest first, 30 per page, keyset-paginated (stable while new payments arrive).
    The first page of a money-role user also carries a summary for the period.
    """

    def get(self, request):
        from apps.payments.models import Payment
        m, show_money = request.merchant, request.member.role in VIEW_MONEY_ROLES
        mode = "test" if request.query_params.get("mode") == "test" else "live"
        qs = Payment.objects.filter(merchant=m, mode=mode)

        status_key = request.query_params.get("status", "")
        if status_key:
            if status_key not in STATUS_FILTERS:
                return Response({"error": "Unknown status filter."}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(status__in=STATUS_FILTERS[status_key])

        period = request.query_params.get("period", "")
        since = None
        if period:
            try:
                since = pagination.period_start(period)
            except KeyError:
                return Response({"error": "Unknown period."}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(created_at__gte=since)

        cursor = request.query_params.get("cursor")
        summary = None
        if not cursor and show_money:
            paid = Payment.objects.filter(merchant=m, mode=mode, status="succeeded")
            if since is not None:
                paid = paid.filter(created_at__gte=since)
            agg = paid.aggregate(n=Count("id"), gross=Sum("amount_minor"), fees=Sum("fee_minor"))
            gross, fees = int(agg["gross"] or 0), int(agg["fees"] or 0)
            summary = {"paid_count": agg["n"], "gross_display": _ghs(gross),
                       "fees_display": _ghs(fees), "net_display": _ghs(gross - fees)}

        try:
            rows, next_cursor = pagination.page(qs, cursor, PAGE_SIZE)
        except pagination.BadCursor:
            return Response({"error": "Invalid cursor."}, status=status.HTTP_400_BAD_REQUEST)
        notes = _notes_for(rows)
        return Response({
            "results": [_payment_json(p, show_money=show_money, request_note=notes.get(p.id, ""))
                        for p in rows],
            "next_cursor": next_cursor,
            "summary": summary,
        })


class PaymentDetailView(MerchantAppView):
    def get(self, request, reference):
        from apps.payments.models import Payment
        p = get_object_or_404(Payment, reference=reference, merchant=request.merchant)
        show_money = request.member.role in VIEW_MONEY_ROLES
        body = _payment_json(p, show_money=show_money, request_note=_notes_for([p]).get(p.id, ""))
        if show_money:
            from . import refunds
            from .dispute_views import dispute_json, refund_json
            from .models import Dispute
            active = p.disputes.filter(status__in=Dispute.ACTIVE).first()
            refundable = refunds.refundable_minor(p) if p.mode == "live" else 0
            body.update(
                refunds=[refund_json(r) for r in p.refunds.order_by("-created_at")],
                refundable_minor=refundable, refundable_display=_ghs(refundable),
                can_refund=bool(refundable and active is None and request.member.role in MOVE_MONEY_ROLES),
                dispute=dispute_json(active, for_merchant=True) if active else None,
            )
        return Response(body)


def _account_json(a: SettlementAccount) -> dict:
    return {"id": str(a.id), "kind": a.kind, "provider": a.provider,
            "account_no": a.account_no, "account_name": a.account_name,
            "verified": a.name_check_status == SettlementAccount.NameCheck.MATCHED,
            "name_check_status": a.name_check_status, "is_default": a.is_default}


def _settlement_json(s: Settlement) -> dict:
    return {"id": str(s.id), "amount_minor": s.amount_minor, "amount_display": _ghs(s.amount_minor),
            "status": s.status, "status_display": s.get_status_display(),
            "destination": f"{s.destination.get_kind_display()} · {s.destination.provider} · "
                           f"{s.destination.account_no}",
            "created_at": s.created_at.isoformat(),
            "completed_at": s.completed_at.isoformat() if s.completed_at else None}


class RequestSettlementSerializer(serializers.Serializer):
    amount = AmountField(required=False, allow_null=True)
    account_id = serializers.UUIDField(required=False)
    pin = serializers.RegexField(r"^\d{6}$")


class SettlementsView(MerchantAppView):
    allowed_roles = VIEW_MONEY_ROLES

    def get(self, request):
        m = request.merchant
        from apps.rails.registry import get_rail
        available = settlement.available_balance_minor(m)
        return Response({
            "available_minor": available,
            "available_display": _ghs(available),
            "can_move_money": request.member.role in MOVE_MONEY_ROLES,
            "bank_payouts_available": bool(get_rail().supports_bank_payout),
            "accounts": [_account_json(a) for a in m.settlement_accounts.order_by("-is_default", "created_at")],
            "settlements": [_settlement_json(s) for s in
                            m.settlements.select_related("destination").order_by("-created_at")[:30]],
        })

    def post(self, request):
        if request.member.role not in MOVE_MONEY_ROLES:
            raise PermissionDenied("Only an Owner or Finance user can request a settlement.")
        if (resp := self._throttled(request, "settlement_request")):
            return resp
        form = RequestSettlementSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        if (resp := self._step_up(request)):
            return resp
        m = request.merchant
        accts = m.settlement_accounts.all()
        acct_id = form.validated_data.get("account_id")
        dest = (accts.filter(pk=acct_id).first() if acct_id
                else accts.filter(is_default=True).first() or accts.first())
        if dest is None:
            return Response({"error": "Add a settlement account first."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            s = settlement.request_settlement(merchant=m, destination=dest, requested_by=request.user,
                                              amount_minor=form.validated_data.get("amount"))
        except (SettlementError, MerchantError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = _settlement_json(s)
        body["needs_approval"] = s.status == Settlement.Status.AWAITING_APPROVAL
        return Response(body, status=status.HTTP_201_CREATED)


class AddAccountSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=SettlementAccount.Kind.values)
    provider = serializers.CharField(max_length=32)
    account_no = serializers.CharField(max_length=32)
    account_name = serializers.CharField(max_length=128)
    pin = serializers.RegexField(r"^\d{6}$")

    def validate(self, data):
        from apps.bulk.validate import normalise_phone
        provider = data["provider"].strip()
        if data["kind"] == SettlementAccount.Kind.MOMO:
            if provider.lower() not in ("mtn", "telecel", "at"):
                raise serializers.ValidationError({"provider": "Network must be mtn, telecel or at."})
            phone = normalise_phone(data["account_no"])
            if phone is None:
                raise serializers.ValidationError({"account_no": "Enter a Ghana phone number, e.g. 0241234567."})
            data["provider"], data["account_no"] = provider.lower(), phone
        else:
            digits = "".join(ch for ch in data["account_no"] if ch.isdigit())
            if not 6 <= len(digits) <= 20 or not provider:
                raise serializers.ValidationError({"account_no": "Enter the bank code and a 6–20 digit account number."})
            data["provider"], data["account_no"] = provider.upper(), digits
        return data


class SettlementAccountsView(MerchantAppView):
    allowed_roles = MOVE_MONEY_ROLES

    def post(self, request):
        if (resp := self._throttled(request, "settlement_account")):
            return resp
        form = AddAccountSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        if (resp := self._step_up(request)):
            return resp
        data = form.validated_data
        m = request.merchant
        if data["kind"] == SettlementAccount.Kind.BANK:
            from apps.rails.registry import get_rail
            if not get_rail().supports_bank_payout:
                return Response({"error": "Bank settlements aren't available yet. Add a "
                                          "mobile-money account for now."},
                                status=status.HTTP_400_BAD_REQUEST)
        # New accounts start unverified (ops check the name) and any settlement to an
        # account changed in the last 72h needs SokoPay approval — see settlement.py.
        acct = SettlementAccount.objects.create(
            merchant=m, kind=data["kind"], provider=data["provider"], account_no=data["account_no"],
            account_name=data["account_name"].strip(),
            is_default=not m.settlement_accounts.exists(),
        )
        from .notifications import settlement_account_added
        settlement_account_added(m, acct, added_by=request.user)   # owner alert if someone else
        return Response(_account_json(acct), status=status.HTTP_201_CREATED)
