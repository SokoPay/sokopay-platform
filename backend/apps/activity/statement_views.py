"""
Statements API.

  GET  /statements?from=YYYY-MM-DD&to=YYYY-MM-DD&file=csv|pdf   customer wallet (JSON without file=)
  GET  /merchant-app/statements?from&to&file                            merchant balance (Owner/Admin/Finance)
  POST /statements/link            {from, to, file: pdf|csv, scope: customer|merchant}
       → {"url": "/api/v1/statements/download?t=<signed single-use token>", "expires_in": 300}
  GET  /statements/download?t=…    the file; no login header needed (token is single-use, 5 min)

The download link exists so a phone can open the PDF in the system browser without
putting the user's login token in a URL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ledger import statements as st
from apps.portal import ratelimit

FORMATS = ("json", "csv", "pdf")
MONEY_ROLES = ("owner", "admin", "finance")


def _merchant_for(user):
    from apps.merchants.models import MerchantMember
    m = MerchantMember.objects.select_related("merchant").filter(user=user, role__in=MONEY_ROLES).first()
    if m is None:
        raise PermissionDenied("Only an Owner, Admin or Finance user can see business statements.")
    return m.merchant


def _build(scope: str, owner, start, end) -> dict:
    return st.merchant_statement(owner, start, end) if scope == "merchant" else st.customer_statement(owner, start, end)


def file_response(statement: dict, fmt: str, scope: str):
    name = f"sokopay-{scope}-statement-{statement['from']}-to-{statement['to']}"
    if fmt == "csv":
        resp = HttpResponse(st.to_csv(statement), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    else:
        resp = HttpResponse(st.to_pdf(statement), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    resp["Cache-Control"] = "no-store"
    return resp


class _StatementBase(APIView):
    permission_classes = [IsAuthenticated]
    scope = "customer"

    def owner(self, request):
        return request.user

    def get(self, request):
        # `file`, not `format`: DRF reserves ?format= for its own content negotiation.
        fmt = request.query_params.get("file", "json")
        if fmt not in FORMATS:
            return Response({"error": "file must be csv or pdf."}, status=status.HTTP_400_BAD_REQUEST)
        if not ratelimit.allow("statement", str(request.user.id)):
            return Response({"error": "Too many statements requested. Please wait."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            start, end = st.parse_period(request.query_params.get("from"), request.query_params.get("to"))
        except st.StatementError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        statement = _build(self.scope, self.owner(request), start, end)
        if fmt == "json":
            return Response(st.to_json(statement))
        return file_response(statement, fmt, self.scope)


class CustomerStatementView(_StatementBase):
    scope = "customer"


class MerchantStatementView(_StatementBase):
    scope = "merchant"

    def owner(self, request):
        return _merchant_for(request.user)


class StatementLinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        scope = request.data.get("scope", "customer")
        fmt = request.data.get("file", "pdf")
        if scope not in ("customer", "merchant") or fmt not in ("csv", "pdf"):
            return Response({"error": "Unknown scope or format."}, status=status.HTTP_400_BAD_REQUEST)
        if not ratelimit.allow("statement", str(request.user.id)):
            return Response({"error": "Too many statements requested. Please wait."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            start, end = st.parse_period(request.data.get("from"), request.data.get("to"))
        except st.StatementError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        owner_id = str(_merchant_for(request.user).id) if scope == "merchant" else str(request.user.id)
        # The token is bound to the requesting user too, so a merchant link can't outlive their role.
        token = st.make_link_token(kind=f"{scope}:{request.user.id}", owner_id=owner_id, start=start, end=end, fmt=fmt)
        return Response({"url": request.build_absolute_uri(f"/api/v1/statements/download?t={token}"),
                         "expires_in": st.LINK_TTL})


class StatementDownloadView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        import datetime as dt
        try:
            data = st.redeem_link_token(request.query_params.get("t", ""))
        except st.StatementError as exc:
            return HttpResponse(str(exc), status=410, content_type="text/plain")
        scope, _, user_id = data["k"].partition(":")
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            return HttpResponse("Invalid download link.", status=410, content_type="text/plain")
        start, end = dt.date.fromisoformat(data["s"]), dt.date.fromisoformat(data["e"])
        if scope == "merchant":
            try:
                merchant = _merchant_for(user)          # re-checked: role may have been removed
            except PermissionDenied:
                return HttpResponse("Not allowed.", status=403, content_type="text/plain")
            if str(merchant.id) != data["o"]:
                return HttpResponse("Not allowed.", status=403, content_type="text/plain")
            statement = st.merchant_statement(merchant, start, end)
        else:
            if str(user.id) != data["o"]:
                return HttpResponse("Not allowed.", status=403, content_type="text/plain")
            statement = st.customer_statement(user, start, end)
        return file_response(statement, data["f"], scope)
