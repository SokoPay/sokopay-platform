"""
notify(): tell a customer something happened.

Writes the message to their in-app inbox immediately (in the caller's transaction, so
it disappears if the money movement rolls back), then pushes it to their phones after
the transaction commits — never before, so a push can't announce money that didn't land.
Pushes are sent by a Celery task so a slow FCM call never delays a payment.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.common.money import Money

from .models import Device, Notification

logger = logging.getLogger("sokopay.push")


def notify(user, *, title: str, body: str, kind: str = Notification.Kind.INFO,
           data: dict | None = None, app: str = "", sms: bool = False) -> Notification | None:
    """
    `data` is the deep-link contract read by the apps — keep values short strings, e.g.
    {"type": "merchant_payment", "reference": "SP-…"}. `app` limits the push to one
    app's devices ("customer" | "agent" | "merchant"; "" = all).
    `sms=True` for important alerts: if no push reaches a phone, an SMS is sent instead.
    """
    if user is None:
        return None
    note = Notification.objects.create(
        user=user, kind=kind, title=title[:80], body=body[:240],
        data={k: str(v) for k, v in (data or {}).items()}, target_app=app, sms_fallback=sms)

    def _enqueue():
        from .tasks import push_notification
        push_notification.delay(str(note.id))

    transaction.on_commit(_enqueue, robust=True)   # a queue outage must not fail the committed action
    return note


def register_device(user, *, platform: str, token: str, app: str) -> Device:
    """Attach a push token to a user. A token moving to a new login moves with it."""
    device, _ = Device.objects.update_or_create(
        token=token, defaults={"user": user, "platform": platform, "app": app, "active": True})
    return device


def unregister_device(user, token: str) -> None:
    Device.objects.filter(user=user, token=token).update(active=False)


def deliver(note: Notification) -> int:
    """Push one notification to all the user's active devices. Returns successes."""
    from .push import get_push_provider
    provider = get_push_provider()
    sent = 0
    devices = Device.objects.filter(user=note.user, active=True)
    if note.target_app:
        devices = devices.filter(app=note.target_app)
    for device in devices:
        result = provider.send(device.token, note.title, note.body,
                               {"kind": note.kind, "id": str(note.id), **note.data})
        if result.success:
            sent += 1
        elif result.invalid_token:
            device.active = False
            device.save(update_fields=["active"])
    if sent:
        note.pushed_at = timezone.now()
        note.save(update_fields=["pushed_at"])
    elif note.sms_fallback:
        send_sms_fallback(note)
    return sent


def sms_text(note: Notification) -> str:
    """One SMS (160 GSM characters): no deep links, no balances beyond what the alert says."""
    text = f"SokoPay: {note.title}. {note.body}".replace("₵", "C").replace("—", "-")
    return text if len(text) <= 160 else text[:157] + "..."


def send_sms_fallback(note: Notification) -> bool:
    """Send the alert by SMS once (atomic claim, so retries never double-send)."""
    from .sms.registry import get_sms_provider
    claimed = Notification.objects.filter(pk=note.pk, sms_sent_at__isnull=True).update(sms_sent_at=timezone.now())
    if not claimed or not note.user.phone:
        return False
    try:
        result = get_sms_provider().send(note.user.phone, sms_text(note))
    except Exception:   # noqa: BLE001 - an SMS outage must not break the push task
        logger.exception("SMS fallback for notification %s failed", note.id)
        Notification.objects.filter(pk=note.pk).update(sms_sent_at=None)
        return False
    if not getattr(result, "success", False):
        Notification.objects.filter(pk=note.pk).update(sms_sent_at=None)
        return False
    return True


# --- helpers used by the money code ---------------------------------------------
def ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()
