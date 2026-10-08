"""
Hosted checkout: the web pages behind payment links, QR codes and API checkout sessions.

Public pages (no login; the payer pays by mobile money, approving the prompt on their phone):
  /m/<short_code>   a shop's counter QR opened in a phone camera: payer enters the amount
  /q/<token>        a dynamic QR / payment request (fixed amount, 15 minutes)
  /l/<token>        a payment link the merchant shared (fixed or open amount)
  /c/<token>        a checkout session created by the merchant's website (API)
  /checkout/status/<reference>   JSON {status} the page polls

Merchant API (API key):
  POST /api/v1/merchant/checkout-sessions  {amount, description?, reference?, success_url?, cancel_url?}
       → {url, token, expires_at}. After payment the payer is sent to success_url?reference=SP-…
       — merchants must confirm by webhook or GET /merchant/charges/<reference>, never by the redirect.

Abuse controls: a MoMo prompt is a push to someone's phone, so prompts are limited per IP
and per payer number; the amount always comes from the server for fixed-amount pages;
CSRF on the form; success/cancel URLs must be https; nothing about the merchant's
balance or other payments is ever shown.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.money import Money, MoneyError
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.payments.models import Payment
from apps.rails.types import Network

from .auth import MerchantApiKeyAuthentication
from .exceptions import MerchantError
from .models import CheckoutSession, Merchant, PaymentLink, PaymentRequest

SESSION_TTL = timedelta(hours=1)
NETWORKS = {"mtn": "MTN MoMo", "telecel": "Telecel Cash", "at": "AT Money"}


class CheckoutError(ValueError):
    pass


def _token() -> str:
    return secrets.token_urlsafe(16)


def _base() -> str:
    return getattr(settings, "QR_BASE_URL", "https://pay.sokopay.com.gh").rstrip("/")


def link_url(link: PaymentLink) -> str:
    return f"{_base()}/l/{link.token}"


def session_url(s: CheckoutSession) -> str:
    return f"{_base()}/c/{s.token}"


def _https(url: str) -> str:
    url = (url or "").strip()
    if url and urlsplit(url).scheme != "https":
        raise CheckoutError("Return URLs must start with https://")
    return url


# --- merchant side --------------------------------------------------------------------------
def create_link(*, merchant: Merchant, title: str, amount_minor: int | None, description: str = "",
                max_uses: int | None = None, created_by) -> PaymentLink:
    if not merchant.is_live:
        raise CheckoutError("Your business must be approved before taking payments.")
    title = (title or "").strip()
    if not title:
        raise CheckoutError("Give the link a title the payer will see.")
    if amount_minor is not None and amount_minor <= 0:
        raise CheckoutError("Amount must be positive.")
    return PaymentLink.objects.create(merchant=merchant, token=_token(), title=title[:80],
                                      description=(description or "")[:240], amount_minor=amount_minor,
                                      max_uses=max_uses, created_by=created_by)


def create_session(*, merchant: Merchant, amount_minor: int, description: str = "", merchant_reference: str = "",
                   success_url: str = "", cancel_url: str = "", mode: str = "live") -> CheckoutSession:
    if not merchant.is_live:
        raise CheckoutError("Your business must be approved before taking payments.")
    if amount_minor <= 0:
        raise CheckoutError("Amount must be positive.")
    return CheckoutSession.objects.create(
        merchant=merchant, token=_token(), amount_minor=amount_minor, description=(description or "")[:120],
        merchant_reference=(merchant_reference or "")[:64], success_url=_https(success_url),
        cancel_url=_https(cancel_url), mode=mode, expires_at=timezone.now() + SESSION_TTL)


# --- resolving a public page to "who, how much" -----------------------------------------------
def _target(kind: str, key: str) -> dict:
    now = timezone.now()
    if kind == "m":
        m = Merchant.objects.filter(short_code=key.upper()).first()
        if m is None or not m.is_live:
            raise Http404
        return {"merchant": m, "amount_minor": None, "description": "", "source": f"shop:{m.short_code}", "open": True}
    if kind == "q":
        r = PaymentRequest.objects.select_related("merchant").filter(token=key).first()
        if r is None:
            raise Http404
        from .qr import expire_if_due
        expire_if_due(r)
        return {"merchant": r.merchant, "amount_minor": r.amount_minor, "description": r.description,
                "source": f"qr:{r.token}", "open": r.status == PaymentRequest.Status.OPEN}
    if kind == "l":
        link = PaymentLink.objects.select_related("merchant").filter(token=key).first()
        if link is None:
            raise Http404
        usable = (link.active and link.merchant.is_live and (link.expires_at is None or link.expires_at > now)
                  and (link.max_uses is None or link.uses < link.max_uses))
        return {"merchant": link.merchant, "amount_minor": link.amount_minor, "title": link.title,
                "description": link.description, "source": f"link:{link.token}", "open": usable}
    if kind == "c":
        s = CheckoutSession.objects.select_related("merchant").filter(token=key).first()
        if s is None:
            raise Http404
        if s.status == CheckoutSession.Status.OPEN and s.expires_at <= now:
            CheckoutSession.objects.filter(pk=s.pk, status="open").update(status="expired")
            s.status = "expired"
        return {"merchant": s.merchant, "amount_minor": s.amount_minor, "description": s.description,
                "source": f"session:{s.token}", "open": s.status == CheckoutSession.Status.OPEN,
                "session": s}
    raise Http404


def start_payment(*, kind: str, key: str, network: str, phone: str, amount_raw: str, ip: str) -> Payment:
    from apps.bulk.validate import normalise_phone
    from apps.portal import ratelimit

    from .checkout import initiate_merchant_charge
    t = _target(kind, key)
    if not t["open"]:
        raise CheckoutError("This payment page is no longer open.")
    if t.get("session") is not None and t["session"].mode != "live":
        raise CheckoutError("This is a test checkout. No real payment can be made.")
    if network not in NETWORKS:
        raise CheckoutError("Choose your mobile money network.")
    payer = normalise_phone(phone or "")
    if payer is None:
        raise CheckoutError("Enter a Ghana mobile money number, e.g. 0241234567.")
    if not ratelimit.allow("checkout_ip", ip or "unknown") or not ratelimit.allow("checkout_phone", payer):
        raise CheckoutError("Too many payment attempts. Please wait a few minutes.")
    amount = t["amount_minor"]
    if amount is None:
        try:
            amount = Money.from_major(amount_raw or "", "GHS").minor
        except MoneyError:
            raise CheckoutError("Enter the amount to pay.") from None
        if amount <= 0:
            raise CheckoutError("Enter the amount to pay.")
    m = t["merchant"]
    try:
        return initiate_merchant_charge(merchant=m, amount_minor=amount, network=Network(network), payer=payer,
                                        narrative=(t.get("title") or t["description"] or m.trading_name or m.legal_name),
                                        source_ref=t["source"])
    except MerchantError as exc:
        raise CheckoutError(str(exc)) from exc


def on_paid(payment: Payment) -> None:
    """A hosted-checkout payment succeeded: close what it paid for."""
    kind, _, token = (payment.source_ref or "").partition(":")
    if kind == "session":
        CheckoutSession.objects.filter(token=token, status__in=("open", "expired")).update(status="paid", payment=payment)
    elif kind == "link":
        PaymentLink.objects.filter(token=token).update(uses=F("uses") + 1)
    elif kind == "qr":
        PaymentRequest.objects.filter(token=token, status=PaymentRequest.Status.OPEN).update(
            status=PaymentRequest.Status.PAID, payment=payment)


# --- public views -------------------------------------------------------------------------------
def _client_ip(request) -> str:
    from apps.portal.ratelimit import client_ip
    return client_ip(request)


def page(request, kind: str, key: str):
    t = _target(kind, key)
    m = t["merchant"]
    ctx = {"t": t, "shop": m.trading_name or m.legal_name, "networks": NETWORKS, "kind": kind, "key": key,
           "amount_display": Money(t["amount_minor"], "GHS").format() if t["amount_minor"] else None,
           "app_code": m.short_code}
    if request.method == "POST":
        try:
            # No transaction here: starting the charge calls the rail over the network.
            p = start_payment(kind=kind, key=key, network=request.POST.get("network", ""),
                              phone=request.POST.get("phone", ""), amount_raw=request.POST.get("amount", ""),
                              ip=_client_ip(request))
            return redirect(f"/checkout/wait/{p.reference}?{urlencode({'k': kind, 't': key})}")
        except CheckoutError as exc:
            ctx["error"] = str(exc)
        except CapabilityNotLicensed:
            ctx["error"] = "Online payments aren't available yet."
    return render(request, "checkout/pay.html", ctx, status=200 if t["open"] else 410)


@require_GET
def wait(request, reference: str):
    p = Payment.objects.select_related("merchant").filter(reference=reference, purpose="merchant").exclude(
        source_ref="").first()
    if p is None:
        raise Http404
    session = None
    kind, _, token = p.source_ref.partition(":")
    if kind == "session":
        session = CheckoutSession.objects.filter(token=token).first()
    return render(request, "checkout/wait.html", {
        "p": p, "shop": p.merchant.trading_name or p.merchant.legal_name,
        "amount_display": Money(p.amount_minor, "GHS").format(), "payer": p.payer_masked,
        "success_url": _with_ref(session.success_url, p.reference) if session and session.success_url else "",
        "cancel_url": session.cancel_url if session else ""})


def _with_ref(url: str, reference: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({'reference': reference})}"


@require_GET
def status_json(request, reference: str):
    p = Payment.objects.filter(reference=reference, purpose="merchant").exclude(source_ref="").only("status").first()
    if p is None:
        raise Http404
    return JsonResponse({"status": p.status})


# --- merchant API: checkout sessions ----------------------------------------------------------------
class _SessionSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(max_length=120, required=False, allow_blank=True)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    success_url = serializers.URLField(max_length=300, required=False, allow_blank=True)
    cancel_url = serializers.URLField(max_length=300, required=False, allow_blank=True)


class CheckoutSessionView(APIView):
    """POST /api/v1/merchant/checkout-sessions (API key)."""

    authentication_classes = [MerchantApiKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = _SessionSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        v = form.validated_data
        key = request.auth
        try:
            s = create_session(merchant=key.merchant, amount_minor=Money.from_major(v["amount"], "GHS").minor,
                               description=v.get("description", ""), merchant_reference=v.get("reference", ""),
                               success_url=v.get("success_url", ""), cancel_url=v.get("cancel_url", ""),
                               mode=key.mode)
        except (CheckoutError, MoneyError) as exc:
            return Response({"error": str(exc) or "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"token": s.token, "url": session_url(s), "expires_at": s.expires_at.isoformat(),
                         "status": s.status}, status=status.HTTP_201_CREATED)
