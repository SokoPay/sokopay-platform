"""
Agent API for the agent mobile app. Authenticated with the consumer JWT (the agent is
a user whose account has an active Agent profile).

Endpoints (/api/v1/agent/…):
  GET  me            → profile + float balance
  POST cash-in       {customer_phone, amount}
  POST cash-out      {customer_phone, amount} → a REQUEST the customer must approve
  GET  cash-out/<id> → its status (pending/approved/declined/expired/failed)
  GET  transactions  → recent agent transactions (home screen)
  GET  history       ?kind=&period=&cursor= → full history, paged, with summary
  GET  transactions/<uuid>                  → one transaction
Customer numbers are masked in all read endpoints.
"""

from __future__ import annotations

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common import pagination
from apps.common.money import Money
from apps.common.pagination import mask_phone

from . import services
from .exceptions import AgentError
from .models import Agent, AgentTxn
from .serializers import CashOpSerializer, TxnSerializer


def _agent_or_none(user) -> Agent | None:
    agent = getattr(user, "agent_profile", None)
    return agent if (agent and agent.is_active) else None


class _AgentView(APIView):
    permission_classes = [IsAuthenticated]

    def get_agent(self, request):
        agent = _agent_or_none(request.user)
        if agent is None:
            return None
        return agent


class AgentMeView(_AgentView):
    def get(self, request):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        balance = services.float_balance(agent)
        from apps.pricing.services import unpaid_minor
        commission = unpaid_minor(agent)
        return Response({
            "display_name": agent.display_name,
            "location": agent.location,
            "float_minor": balance,
            "float_display": Money(balance, "GHS").format(),
            # Earned and not yet paid into the float (paid monthly).
            "commission_minor": commission,
            "commission_display": Money(commission, "GHS").format(),
        })


class CashInView(_AgentView):
    def post(self, request):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        form = CashOpSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            txn = services.cash_in(
                agent=agent,
                customer_phone=form.validated_data["customer"],
                amount_minor=Money.from_major(form.validated_data["amount"], "GHS").minor,
            )
        except AgentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # CapabilityNotLicensed → 403 via the project exception handler.
        return Response(TxnSerializer(txn).data, status=status.HTTP_201_CREATED)


class CashOutView(_AgentView):
    def post(self, request):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        form = CashOpSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            req = services.request_cash_out(
                agent=agent,
                customer_phone=form.validated_data["customer"],
                amount_minor=Money.from_major(form.validated_data["amount"], "GHS").minor,
            )
        except AgentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # Nothing has moved yet: the customer must approve in their app.
        return Response(_cash_out_json(req), status=status.HTTP_201_CREATED)


def _cash_out_json(req, *, for_customer: bool = False) -> dict:
    body = {
        "id": str(req.id),
        "status": req.status,
        "status_display": req.get_status_display(),
        "amount_minor": req.amount_minor,
        "amount_display": Money(req.amount_minor, "GHS").format(),
        "fee_minor": req.fee_minor,
        "fee_display": Money(req.fee_minor, "GHS").format(),
        "total_display": Money(req.amount_minor + req.fee_minor, "GHS").format(),   # what leaves the wallet
        "expires_at": req.expires_at.isoformat(),
        "reason": req.failure_reason,
    }
    if for_customer:
        body.update(agent_name=req.agent.display_name, agent_location=req.agent.location)
    else:
        body["customer"] = mask_phone(req.customer.phone)
    return body


class CustomerLookupView(_AgentView):
    """GET /agent/customer?account=<phone or wallet ID> — name to read back before crediting."""

    def get(self, request):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        try:
            return Response(services.lookup_customer(request.query_params.get("account", "")))
        except AgentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)


class CashOutStatusView(_AgentView):
    """GET /agent/cash-out/<id> — polled by the agent app until the customer decides."""

    def get(self, request, pk):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        req = services.agent_cash_out_status(agent, pk)
        if req is None:
            return Response({"error": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_cash_out_json(req))


# --- customer side: "Allow CashOut" ---------------------------------------------------
def _window_json(w) -> dict | None:
    return None if w is None else {"expires_at": w.expires_at.isoformat()}


class CustomerCashOutsView(APIView):
    """
    GET /wallet/cash-out-requests — my open "Cash out" window (if any) and the agent
    requests waiting for my PIN. Polled by the Allow CashOut screen.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "window": _window_json(services.open_window(request.user)),
            "requests": [_cash_out_json(r, for_customer=True)
                         for r in services.pending_cash_outs(request.user)],
        })


class CashOutWindowView(APIView):
    """POST /wallet/cash-out/allow — "I'm at an agent, let them request a cash-out."
       DELETE /wallet/cash-out/allow — close it again."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        w = services.open_cash_out_window(request.user)
        return Response(_window_json(w), status=status.HTTP_201_CREATED)

    def delete(self, request):
        services.close_cash_out_window(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerCashOutDecisionView(APIView):
    """POST /wallet/cash-out-requests/<id>/approve {pin}  |  …/decline"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk, decision):
        try:
            if decision == "approve":
                req = services.approve_cash_out(customer=request.user, request_id=pk,
                                                pin=str(request.data.get("pin", "")))
            elif decision == "decline":
                req = services.decline_cash_out(customer=request.user, request_id=pk)
            else:
                return Response({"error": "Unknown action."}, status=status.HTTP_404_NOT_FOUND)
        except AgentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        body = _cash_out_json(req, for_customer=True)
        code = status.HTTP_200_OK if req.status != "failed" else status.HTTP_400_BAD_REQUEST
        if req.status == "failed":
            body["error"] = req.failure_reason
        return Response(body, status=code)


class AgentTxnsView(_AgentView):
    """Recent transactions for the home screen (customer numbers masked)."""

    def get(self, request):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        txns = agent.transactions.order_by("-created_at")[:20]
        return Response([{"kind": t.kind, "customer_phone": mask_phone(t.customer_phone) if t.customer_phone else "",
                          "amount_minor": t.amount_minor, "created_at": t.created_at} for t in txns])


# --- full history ------------------------------------------------------------------
HISTORY_PAGE = 30
KIND_FILTERS = {"cash_in", "cash_out", "topup"}
# How each kind changes the agent's float: cash-in spends float (customer's wallet is
# credited from it); cash-out and top-ups add to it.
FLOAT_SIGN = {"cash_in": -1, "cash_out": +1, "topup": +1}


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


def _txn_json(t) -> dict:
    sign = FLOAT_SIGN.get(t.kind, 0)
    return {
        "id": str(t.id),
        "reference": "AG-" + t.id.hex[:10].upper(),     # short code to quote to support
        "kind": t.kind,
        "kind_display": t.get_kind_display(),
        "amount_minor": t.amount_minor,
        "amount_display": _ghs(t.amount_minor),
        "float_effect_display": ("+ " if sign > 0 else "− ") + _ghs(t.amount_minor),
        "customer": mask_phone(t.customer_phone) if t.customer_phone else "",
        "created_at": t.created_at.isoformat(),
    }


class AgentHistoryView(_AgentView):
    """
    GET /agent/history?kind=cash_in|cash_out|topup&period=today|7d|30d&cursor=<opaque>
    Newest first, 30 per page, keyset-paginated. The first page carries a summary
    for the period (totals per kind) and the current float.
    """

    def get(self, request):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        qs = agent.transactions.all()
        kind = request.query_params.get("kind", "")
        if kind:
            if kind not in KIND_FILTERS:
                return Response({"error": "Unknown kind."}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(kind=kind)
        period = request.query_params.get("period", "")
        period_qs = agent.transactions.all()
        if period:
            try:
                since = pagination.period_start(period)
            except KeyError:
                return Response({"error": "Unknown period."}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(created_at__gte=since)
            period_qs = period_qs.filter(created_at__gte=since)

        cursor = request.query_params.get("cursor")
        summary = None
        if not cursor:
            totals = {row["kind"]: row for row in
                      period_qs.values("kind").annotate(n=Count("id"), total=Sum("amount_minor"))}
            summary = {
                k: {"count": totals.get(k, {}).get("n", 0),
                    "total_display": _ghs(totals.get(k, {}).get("total") or 0)}
                for k in ("cash_in", "cash_out", "topup")
            }
            summary["float_display"] = _ghs(services.float_balance(agent))

        try:
            rows, next_cursor = pagination.page(qs, cursor, HISTORY_PAGE)
        except pagination.BadCursor:
            return Response({"error": "Invalid cursor."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"results": [_txn_json(t) for t in rows], "next_cursor": next_cursor,
                         "summary": summary})


class AgentTxnDetailView(_AgentView):
    def get(self, request, pk):
        agent = self.get_agent(request)
        if agent is None:
            return Response({"error": "Not an active agent."}, status=status.HTTP_403_FORBIDDEN)
        txn = get_object_or_404(AgentTxn, pk=pk, agent=agent)     # only your own
        return Response(_txn_json(txn))
