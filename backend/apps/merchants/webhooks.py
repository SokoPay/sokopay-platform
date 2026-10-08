"""
Outbound merchant webhooks: tell a merchant's server when something happens.

Events: payment.succeeded, payment.failed, refund.succeeded, refund.failed,
settlement.paid, settlement.failed, dispute.opened, dispute.resolved.

Body (JSON):
    {"id": "evt_…", "type": "payment.succeeded", "created": "<ISO-8601>",
     "mode": "live", "data": {...}}

Headers:
    SokoPay-Event:     payment.succeeded
    SokoPay-Delivery:  <delivery uuid>  (retries of one event keep the same event id)
    SokoPay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>

Merchants must verify the signature, reject timestamps older than 5 minutes (replay),
and de-duplicate on the event id (at-least-once delivery). Then re-fetch the payment
from the API before shipping goods: a webhook is a hint, not proof.

Security on OUR side (server-side request forgery):
  * https only; the hostname is resolved and EVERY address must be public (no
    loopback, private, link-local, multicast, reserved or cloud-metadata ranges).
  * The connection is made to the address we checked (pinned), with the original
    hostname for TLS/SNI, so DNS can't be switched to an internal address after the
    check (DNS rebinding). Redirects are not followed. Responses are read up to 4 KB.
  * WEBHOOK_ALLOW_INSECURE (dev only) allows http and private addresses.

Retries: 1m, 5m, 30m, 2h, 6h, 12h, 24h, then abandoned. An endpoint that fails
DISABLE_AFTER times in a row is switched off and the owner is told.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.common.audit import audited

from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger("sokopay.webhooks")

BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
           timedelta(hours=6), timedelta(hours=12), timedelta(hours=24)]
DISABLE_AFTER = 50
TIMEOUT_SECONDS = 10
MAX_ENDPOINTS = 5
USER_AGENT = "SokoPay-Webhooks/1.0"


class WebhookUrlError(ValueError):
    pass


# --- endpoint management -------------------------------------------------------------------
def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def _allow_insecure() -> bool:
    return bool(getattr(settings, "WEBHOOK_ALLOW_INSECURE", False))


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified or not addr.is_global)


def check_url(url: str) -> tuple[str, int, list[str]]:
    """Validate a webhook URL; returns (hostname, port, public IPs). Raises WebhookUrlError."""
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https" and not (_allow_insecure() and parts.scheme == "http"):
        raise WebhookUrlError("Webhook URLs must use https://")
    if not parts.hostname or parts.username or parts.password:
        raise WebhookUrlError("Enter a full URL like https://example.com/sokopay/webhook (no user:password).")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise WebhookUrlError("That port isn't valid.") from None
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise WebhookUrlError("We couldn't find that server (DNS lookup failed).") from None
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise WebhookUrlError("We couldn't find that server.")
    if not _allow_insecure() and not all(_public(ip) for ip in ips):
        raise WebhookUrlError("Webhook URLs must point to a public internet address.")
    return parts.hostname, port, ips


@audited("webhook.add", actor="created_by", fields=("url",))
def add_endpoint(*, merchant, url: str, events: list[str] | None, created_by) -> WebhookEndpoint:
    if merchant.webhook_endpoints.filter(active=True).count() >= MAX_ENDPOINTS:
        raise WebhookUrlError(f"You can have at most {MAX_ENDPOINTS} active webhook URLs.")
    events = [e for e in (events or []) if e in WebhookEndpoint.EVENTS]
    check_url(url)
    return WebhookEndpoint.objects.create(merchant=merchant, url=url.strip(), secret=new_secret(),
                                          events=events, created_by=created_by)


def rotate_secret(endpoint: WebhookEndpoint) -> str:
    endpoint.secret = new_secret()
    endpoint.save(update_fields=["secret", "updated_at"])
    return endpoint.secret


# --- payloads -----------------------------------------------------------------------------
def payment_payload(p) -> dict:
    return {"reference": p.reference, "status": p.status, "amount_minor": p.amount_minor,
            "fee_minor": p.fee_minor, "net_minor": p.amount_minor - p.fee_minor, "currency": p.currency,
            "method": "wallet" if p.funding_source == "wallet" else p.network, "payer": p.payer_masked,
            "created_at": p.created_at.isoformat(),
            "completed_at": p.completed_at.isoformat() if p.completed_at else None}


def refund_payload(r) -> dict:
    return {"id": str(r.id), "payment_reference": r.payment.reference, "status": r.status,
            "amount_minor": r.amount_minor, "currency": r.currency, "reason": r.reason}


def settlement_payload(s) -> dict:
    return {"id": str(s.id), "status": s.status, "amount_minor": s.amount_minor, "currency": s.currency}


def dispute_payload(d) -> dict:
    return {"id": str(d.id), "payment_reference": d.payment.reference, "status": d.status,
            "reason": d.reason, "amount_minor": d.amount_minor, "respond_by": d.respond_by.isoformat()}


# --- emitting -----------------------------------------------------------------------------
def emit(merchant, event: str, data: dict, *, mode: str = "live") -> int:
    """Queue `event` for every endpoint that wants it. Safe inside a transaction: the
    deliveries are only sent once it commits. Never raises into the money path."""
    if merchant is None or event not in WebhookEndpoint.EVENTS:
        return 0
    try:
        endpoints = [e for e in merchant.webhook_endpoints.filter(active=True) if e.wants(event)]
        if not endpoints:
            return 0
        event_id = "evt_" + uuid.uuid4().hex
        body = {"id": event_id, "type": event, "created": timezone.now().isoformat(), "mode": mode, "data": data}
        ids = [WebhookDelivery.objects.create(endpoint=e, event_id=event_id, event_type=event, payload=body,
                                              next_attempt_at=timezone.now()).id for e in endpoints]
    except Exception:   # noqa: BLE001 - a webhook problem must never undo a payment
        logger.exception("Could not queue webhook %s for merchant %s", event, merchant.id)
        return 0

    def _send():
        from .tasks import deliver_webhook
        for i in ids:
            deliver_webhook.delay(str(i))
    transaction.on_commit(_send, robust=True)   # a queue outage must not fail the committed action
    return len(ids)


# --- delivering ---------------------------------------------------------------------------
def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def _post(url: str, hostname: str, port: int, ip: str, body: bytes, headers: dict) -> int:
    """POST to the pinned IP with the real hostname for TLS. Returns the status code."""
    import certifi
    import urllib3
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    host_header = hostname if port in (80, 443) else f"{hostname}:{port}"
    timeout = urllib3.Timeout(connect=5, read=TIMEOUT_SECONDS)
    if parts.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(ip, port, server_hostname=hostname, assert_hostname=hostname,
                                           cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
                                           timeout=timeout, retries=False, maxsize=1)
    else:
        pool = urllib3.HTTPConnectionPool(ip, port, timeout=timeout, retries=False, maxsize=1)
    try:
        resp = pool.urlopen("POST", path, body=body, headers={**headers, "Host": host_header},
                            redirect=False, preload_content=False)
        resp.read(4096, decode_content=False)
        resp.release_conn()
        return resp.status
    finally:
        pool.close()


def deliver(delivery_id) -> WebhookDelivery:
    with transaction.atomic():
        d = WebhookDelivery.objects.select_for_update(of=("self",)).select_related("endpoint", "endpoint__merchant") \
            .get(pk=delivery_id)
        if d.status in (WebhookDelivery.Status.SUCCEEDED, WebhookDelivery.Status.ABANDONED):
            return d
        if not d.endpoint.active:
            d.status, d.last_error = WebhookDelivery.Status.ABANDONED, "Endpoint disabled"
            d.save(update_fields=["status", "last_error", "updated_at"])
            return d
        d.attempts += 1
        # Claim the attempt so a parallel worker doesn't send it at the same time.
        d.next_attempt_at = timezone.now() + BACKOFF[min(d.attempts - 1, len(BACKOFF) - 1)]
        d.save(update_fields=["attempts", "next_attempt_at", "updated_at"])

    ep = d.endpoint
    body = json.dumps(d.payload, separators=(",", ":"), sort_keys=True).encode()
    ts = int(time.time())
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
               "SokoPay-Event": d.event_type, "SokoPay-Delivery": str(d.id),
               "SokoPay-Signature": sign(ep.secret, ts, body)}
    code, error = None, ""
    try:
        hostname, port, ips = check_url(ep.url)      # re-checked every time: DNS can change
        code = _post(ep.url, hostname, port, ips[0], body, headers)
        if not 200 <= code < 300:
            error = f"HTTP {code}"
    except WebhookUrlError as exc:
        error = str(exc)
    except Exception as exc:   # noqa: BLE001 - network errors of every kind are a failed attempt
        error = f"{type(exc).__name__}: {exc}"[:255]

    with transaction.atomic():
        d = WebhookDelivery.objects.select_for_update().get(pk=d.pk)
        ep = WebhookEndpoint.objects.select_for_update().get(pk=ep.pk)
        d.last_status_code, d.last_error = code, error[:255]
        if not error:
            d.status, d.delivered_at, d.next_attempt_at = WebhookDelivery.Status.SUCCEEDED, timezone.now(), None
            ep.consecutive_failures = 0
        else:
            ep.consecutive_failures += 1
            if d.attempts > len(BACKOFF):
                d.status, d.next_attempt_at = WebhookDelivery.Status.ABANDONED, None
            else:
                d.status = WebhookDelivery.Status.FAILED
            if ep.active and ep.consecutive_failures >= DISABLE_AFTER:
                ep.active, ep.disabled_reason = False, f"Switched off after {DISABLE_AFTER} failed deliveries"
                _tell_owner_disabled(ep)
        d.save()
        ep.save(update_fields=["consecutive_failures", "active", "disabled_reason", "updated_at"])
    return d


def retry_due(limit: int = 500) -> int:
    due = WebhookDelivery.objects.filter(
        status__in=(WebhookDelivery.Status.PENDING, WebhookDelivery.Status.FAILED),
        next_attempt_at__lte=timezone.now()).order_by("next_attempt_at").values_list("id", flat=True)[:limit]
    n = 0
    for i in due:
        deliver(i)
        n += 1
    return n


def send_test(endpoint: WebhookEndpoint) -> WebhookDelivery:
    body = {"id": "evt_" + uuid.uuid4().hex, "type": "test", "created": timezone.now().isoformat(),
            "mode": "test", "data": {"message": "Hello from SokoPay"}}
    d = WebhookDelivery.objects.create(endpoint=endpoint, event_id=body["id"], event_type="test", payload=body,
                                       next_attempt_at=timezone.now())
    d = deliver(d.id)
    if d.status != WebhookDelivery.Status.SUCCEEDED:   # a test isn't retried
        WebhookDelivery.objects.filter(pk=d.pk).update(status=WebhookDelivery.Status.ABANDONED, next_attempt_at=None)
        d.refresh_from_db()
    return d


def _tell_owner_disabled(ep: WebhookEndpoint) -> None:
    from apps.notifications.services import notify
    notify(ep.merchant.owner, kind="security", title="Webhook switched off", app="merchant",
           body=f"{ep.url} failed {DISABLE_AFTER} times in a row and was switched off. "
                "Fix it and turn it back on in the merchant portal.",
           data={"type": "settlement_accounts"})
