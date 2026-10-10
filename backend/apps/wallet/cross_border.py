"""
Cross-border send: quote → confirm with PIN → send → delivered or refunded.

The partner (settings.CROSS_BORDER_PROVIDER: Onafriq or Brij once contracted; "mock" in
dev) does the FX and pays out abroad. SokoPay holds nothing across the border.

Money (same reserve → send → resolve pattern as interop transfers):
  1. reserve:  Dr customer_wallet(total)   Cr cross_border_in_flight   (committed first)
  2. partner call, outside any DB transaction
  3a. delivered: Dr cross_border_in_flight  Cr partner_clearing:xb-<provider>
  3b. refused:   Dr cross_border_in_flight  Cr customer_wallet          (full refund)
  PENDING / UNKNOWN: wait for the poller; never refund money that may still arrive.

Controls:
  * Licence CROSS_BORDER_TRANSFER (+ wallet); sender must be Ghana-Card verified.
  * A quote is bound to the customer, valid until its expiry, and usable once.
  * PIN again at send (money leaving the country).
  * Recipient name screened against the sanctions lists; a hit is refused with a
    neutral message and raises an AML alert (no tipping-off).
  * Tier limits apply to the total; AML monitoring sees the wallet debit like any other.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.connectors.registry import UnknownConnector, get_connector
from apps.connectors.types import CrossBorderRecipient, CrossBorderRequest
from apps.kyc import limits as kyc_limits
from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.rails.types import RailStatus

from .exceptions import WalletError
from .models import CrossBorderTransfer

logger = logging.getLogger("sokopay.cross_border")

MIN_SEND_MINOR = 10_00
MAX_SEND_MINOR = 5_000_00          # per transfer [VERIFY with BoG approval / partner limits]
PURPOSES = {
    "family_support": "Family support",
    "education": "School fees / education",
    "medical": "Medical",
    "goods_services": "Payment for goods or services",
    "gift": "Gift",
    "savings": "Savings / investment",
}
METHODS = ("mobile_money", "bank")
_Q = "xbq:"
_REF = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CrossBorderError(WalletError):
    pass


def provider_key() -> str:
    return getattr(settings, "CROSS_BORDER_PROVIDER", "") or ""


def _connector():
    key = provider_key()
    if not key:
        raise CrossBorderError("Sending abroad isn't available yet.")
    try:
        c = get_connector("cross_border", key)
    except UnknownConnector:
        raise CrossBorderError("Sending abroad isn't available yet.") from None
    if not getattr(c, "available", False):
        raise CrossBorderError("Sending abroad isn't available yet.")
    return c


def corridors() -> dict:
    try:
        c = _connector()
    except CrossBorderError as exc:
        return {"available": False, "message": str(exc), "countries": []}
    return {"available": True, "partner": c.display_name, "countries": sorted(c.corridors),
            "purposes": PURPOSES, "methods": list(METHODS),
            "min_display": ghs(MIN_SEND_MINOR), "max_display": ghs(MAX_SEND_MINOR)}


def _require_allowed(user) -> None:
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    require_capability(Capability.CROSS_BORDER_TRANSFER)
    profile = kyc_limits.profile_for(user)
    if profile.tier < 1:
        raise CrossBorderError("Verify your Ghana Card to send money abroad.")
    if profile.frozen:
        raise CrossBorderError("Your wallet can't send money right now. Contact support.")


def get_quote(*, user, country: str, method: str, account: str, institution: str, name: str,
              amount_minor: int) -> dict:
    _require_allowed(user)
    c = _connector()
    country = (country or "").upper()
    if country not in c.corridors:
        raise CrossBorderError("We can't send to that country yet.")
    if method not in METHODS:
        raise CrossBorderError("Choose mobile money or bank.")
    name, account, institution = (name or "").strip(), (account or "").strip(), (institution or "").strip()
    if len(name.split()) < 2 or not account or not institution:
        raise CrossBorderError("Enter the recipient's full name, provider and account.")
    if not MIN_SEND_MINOR <= amount_minor <= MAX_SEND_MINOR:
        raise CrossBorderError(f"Send between {ghs(MIN_SEND_MINOR)} and {ghs(MAX_SEND_MINOR)}.")
    recipient = CrossBorderRecipient(country=country, method=method, account=account,
                                     institution=institution, name=name)
    q = c.quote(amount_minor, recipient)
    expires = parse_datetime(q.expires_at) or timezone.now()
    ttl = max(1, int((expires - timezone.now()).total_seconds()))
    cache.set(_Q + q.quote_id, {
        "user": str(user.id), "provider": c.key, "amount_minor": amount_minor, "fee_minor": q.fee_minor,
        "total_minor": q.send_minor, "receive_amount": q.receive_amount, "receive_currency": q.receive_currency,
        "rate": q.rate, "expires_at": q.expires_at, "country": country, "method": method, "account": account,
        "institution": institution, "name": name,
    }, timeout=ttl)
    return {"quote_id": q.quote_id, "amount_display": ghs(amount_minor), "fee_display": ghs(q.fee_minor),
            "total_minor": q.send_minor, "total_display": ghs(q.send_minor),
            "receive_amount": q.receive_amount, "receive_currency": q.receive_currency, "rate": q.rate,
            "expires_at": q.expires_at, "recipient_name": name}


def _new_reference() -> str:
    return "XB-" + "".join(secrets.choice(_REF) for _ in range(10))


@kyc_limits.debits_serialized("user")
def send(*, user, quote_id: str, purpose: str, pin: str) -> CrossBorderTransfer:
    from apps.accounts import services as auth_services
    try:
        auth_services.confirm_pin(user, pin)
    except auth_services.AuthError as exc:
        raise CrossBorderError(str(exc)) from exc
    _require_allowed(user)
    if purpose not in PURPOSES:
        raise CrossBorderError("Choose the reason for this transfer.")
    q = cache.get(_Q + (quote_id or ""))
    if not q or q["user"] != str(user.id):
        raise CrossBorderError("This quote has expired. Get a new one.")
    if not cache.add(f"xbq-used:{quote_id}", 1, timeout=3600):
        raise CrossBorderError("This quote has already been used.")
    cache.delete(_Q + quote_id)
    c = _connector()
    if c.key != q["provider"]:
        raise CrossBorderError("This quote has expired. Get a new one.")

    # Sanctions screening on the recipient (the sender was screened at KYC).
    from apps.compliance.screening import match_name
    hits = match_name(q["name"])
    if hits:
        from apps.compliance import alerts
        top = hits[0]
        alerts.raise_alert(rule="SANCTIONS_MATCH", title="Cross-border recipient matches a watchlist",
                           severity="high", subject_kind="customer", subject_id=str(user.id),
                           subject_label=user.full_name or user.phone, user=user,
                           evidence={"recipient": q["name"], "country": q["country"],
                                     "list_entry": top[0].name, "score": f"{top[1]:.2f}"})
        raise CrossBorderError("We can't send this transfer. Contact SokoPay support.")

    total = int(q["total_minor"])
    kyc_limits.check_debit(user, total)
    wallet = accounts.customer_wallet(str(user.id))
    if natural_balance_of(wallet) < total:
        raise CrossBorderError("You don't have enough in your wallet for this transfer and its fee.")

    with transaction.atomic():
        t = CrossBorderTransfer.objects.create(
            reference=_new_reference(), sender=user, provider=c.key, quote_id=quote_id, country=q["country"],
            method=q["method"], account=q["account"], institution=q["institution"], recipient_name=q["name"],
            purpose=purpose, amount_minor=q["amount_minor"], fee_minor=q["fee_minor"], total_minor=total,
            receive_amount=q["receive_amount"], receive_currency=q["receive_currency"], rate=q["rate"])
        try:
            post_entry(f"Cross-border send {t.reference} to {q['country']}",
                       [debit(wallet, total), credit(accounts.cross_border_in_flight(), total)],
                       idempotency_key=f"xb-reserve:{t.id}", reference=("cross_border", str(t.id)))
        except InsufficientFunds as exc:
            raise CrossBorderError("You don't have enough in your wallet for this transfer and its fee.") from exc

    try:
        result = c.send(CrossBorderRequest(
            quote_id=quote_id, sender_name=user.full_name or "",
            recipient=CrossBorderRecipient(country=t.country, method=t.method, account=t.account,
                                           institution=t.institution, name=t.recipient_name),
            purpose=purpose, reference=t.reference, idempotency_key=f"xb-send:{t.id}"))
    except Exception as exc:   # noqa: BLE001 - we can't know if it left: wait, don't refund
        logger.error("Cross-border %s send raised: %s", t.reference, exc)
        return t
    CrossBorderTransfer.objects.filter(pk=t.pk).update(provider_ref=(result.provider_ref or "")[:64])
    t.refresh_from_db()
    return apply_outcome(t, result.status, result.failure_code)


def apply_outcome(t: CrossBorderTransfer, status: RailStatus, failure_code: str = "") -> CrossBorderTransfer:
    with transaction.atomic():
        t = CrossBorderTransfer.objects.select_for_update().get(pk=t.pk)
        if t.status != CrossBorderTransfer.Status.PENDING:
            return t
        in_flight = accounts.cross_border_in_flight()
        if status == RailStatus.SUCCEEDED:
            clearing = accounts.partner_clearing(f"xb-{t.provider}")
            post_entry(f"Cross-border {t.reference} delivered", [debit(in_flight, t.total_minor),
                                                                  credit(clearing, t.total_minor)],
                       idempotency_key=f"xb-settle:{t.id}", reference=("cross_border", str(t.id)),
                       allow_negative={clearing.code})
            t.status = CrossBorderTransfer.Status.SUCCEEDED
            title, body = "Money delivered", (f"{t.receive_amount} {t.receive_currency} reached "
                                              f"{t.recipient_name}.")
        elif status == RailStatus.FAILED:
            post_entry(f"Cross-border {t.reference} refused, refund",
                       [debit(in_flight, t.total_minor), credit(accounts.customer_wallet(str(t.sender_id)),
                                                                t.total_minor)],
                       idempotency_key=f"xb-refund:{t.id}", reference=("cross_border", str(t.id)))
            t.status, t.failure_code = CrossBorderTransfer.Status.FAILED, (failure_code or "declined")[:64]
            title, body = "Transfer refunded", (f"Your transfer to {t.recipient_name} couldn't be delivered. "
                                                f"{ghs(t.total_minor)} is back in your wallet.")
        else:
            return t
        t.completed_at = timezone.now()
        t.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])
        notify(t.sender, kind="wallet", title=title, body=body, app="customer", data={"type": "wallet"})
    return t


def resolve_pending(limit: int = 300) -> dict:
    resolved = 0
    for t in CrossBorderTransfer.objects.filter(status="pending").exclude(provider_ref="").order_by("updated_at")[:limit]:
        try:
            st = get_connector("cross_border", t.provider).get_status(t.provider_ref)
        except Exception as exc:   # noqa: BLE001
            logger.warning("Cross-border %s status check failed: %s", t.reference, exc)
            continue
        if apply_outcome(t, st.status, st.failure_code).status != "pending":
            resolved += 1
    return {"resolved": resolved}


def transfer_json(t: CrossBorderTransfer) -> dict:
    return {"reference": t.reference, "status": t.status, "status_display": t.get_status_display(),
            "country": t.country, "method": t.method, "recipient_name": t.recipient_name,
            "account": t.account[:3] + "•••" + t.account[-3:] if len(t.account) > 6 else t.account,
            "amount_display": ghs(t.amount_minor), "fee_display": ghs(t.fee_minor),
            "total_display": ghs(t.total_minor), "receive": f"{t.receive_amount} {t.receive_currency}",
            "rate": t.rate, "purpose": PURPOSES.get(t.purpose, t.purpose),
            "created_at": t.created_at.isoformat()}
