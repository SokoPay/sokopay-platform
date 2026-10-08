"""
QR codes and payment requests for merchants.

Two kinds of QR:
  * STATIC  — one per merchant, printed on a sticker. Encodes the merchant's short
              code; the customer enters the amount.
              Payload: <QR_BASE_URL>/m/<SHORT_CODE>
  * DYNAMIC — created per sale with the amount; shown on the merchant's screen or sent
              as a link. Expires after a short window.
              Payload: <QR_BASE_URL>/q/<TOKEN>

Both payloads are URLs so that a phone's normal camera opens the SokoPay web page
(not built yet) while the SokoPay app recognises and handles them directly. The
resolver also accepts a bare short code typed by the customer.
"""

from __future__ import annotations

import datetime as dt
import secrets

import segno
from django.conf import settings
from django.utils import timezone

from .exceptions import MerchantError
from .models import Merchant, PaymentRequest

# Crockford base32: no I, L, O or U, so codes survive being read aloud or typed.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
REQUEST_TTL = dt.timedelta(minutes=15)


def _base_url() -> str:
    return getattr(settings, "QR_BASE_URL", "https://pay.sokopay.com.gh").rstrip("/")


def ensure_short_code(merchant: Merchant) -> str:
    if merchant.short_code:
        return merchant.short_code
    for _ in range(20):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if not Merchant.objects.filter(short_code=code).exists():
            merchant.short_code = code
            merchant.save(update_fields=["short_code", "updated_at"])
            return code
    raise MerchantError("Could not allocate a merchant code; try again.")


def static_payload(merchant: Merchant) -> str:
    return f"{_base_url()}/m/{ensure_short_code(merchant)}"


def request_payload(request: PaymentRequest) -> str:
    return f"{_base_url()}/q/{request.token}"


def qr_svg_data_uri(payload: str, scale: int = 6) -> str:
    """An inline SVG the portal can put straight into an <img src>."""
    return segno.make(payload, error="m").svg_data_uri(scale=scale)


def create_payment_request(*, merchant: Merchant, created_by, amount_minor: int | None,
                           description: str = "") -> PaymentRequest:
    if not merchant.is_live:
        raise MerchantError("Merchant is not approved to take payments.")
    if amount_minor is not None and amount_minor <= 0:
        raise MerchantError("Amount must be positive.")
    return PaymentRequest.objects.create(
        merchant=merchant,
        token=secrets.token_urlsafe(16),
        amount_minor=amount_minor,
        description=description[:120],
        expires_at=timezone.now() + REQUEST_TTL,
        created_by=created_by,
    )


def expire_if_due(request: PaymentRequest) -> PaymentRequest:
    if request.status == PaymentRequest.Status.OPEN and request.expires_at <= timezone.now():
        request.status = PaymentRequest.Status.EXPIRED
        request.save(update_fields=["status", "updated_at"])
    return request


def resolve(code: str) -> dict:
    """
    Turn whatever was scanned or typed into "who am I paying, and how much".

    Accepts the full URL payloads, a bare request token, or a bare short code.
    Returns a dict the app renders as a confirmation screen; raises MerchantError
    for anything unknown, so a tampered or foreign QR never reaches a payment.
    """
    value = (code or "").strip()
    if not value or len(value) > 200:
        raise MerchantError("That code isn't a SokoPay payment code.")

    token = short = None
    if "/q/" in value:
        token = value.rsplit("/q/", 1)[1].split("?")[0].split("#")[0]
    elif "/m/" in value:
        short = value.rsplit("/m/", 1)[1].split("?")[0].split("#")[0]
    elif len(value) <= 8:
        short = value.upper()
    else:
        token = value

    if token:
        request = PaymentRequest.objects.select_related("merchant").filter(token=token).first()
        if request is None:
            raise MerchantError("That payment code isn't recognised.")
        expire_if_due(request)
        return {
            "type": "request",
            "merchant_name": request.merchant.trading_name or request.merchant.legal_name,
            "merchant_code": ensure_short_code(request.merchant),
            "amount_minor": request.amount_minor,
            "description": request.description,
            "request_token": request.token,
            "status": request.status,
        }

    merchant = Merchant.objects.filter(short_code=short).first()
    if merchant is None or not merchant.is_live:
        raise MerchantError("That merchant code isn't recognised.")
    return {
        "type": "static",
        "merchant_name": merchant.trading_name or merchant.legal_name,
        "merchant_code": merchant.short_code,
        "amount_minor": None,
        "description": "",
        "request_token": None,
        "status": "open",
    }
