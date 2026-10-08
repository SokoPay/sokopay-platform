"""
Push devices and the in-app notification inbox.

A Device is one phone's push token (Firebase Cloud Messaging on both Android and
iOS). A Notification is the message we sent — kept so the app can show an inbox and
so support can see what a customer was told.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Device(TimeStampedModel):
    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    class App(models.TextChoices):
        CUSTOMER = "customer", "Customer app"
        AGENT = "agent", "Agent app"
        MERCHANT = "merchant", "Merchant app"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="devices")
    platform = models.CharField(max_length=8, choices=Platform.choices)
    app = models.CharField(max_length=8, choices=App.choices, default=App.CUSTOMER)
    token = models.CharField(max_length=512, unique=True)
    active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notifications_device"
        indexes = [models.Index(fields=["user", "active"])]

    def __str__(self) -> str:
        return f"{self.user} {self.platform}/{self.app} ({'active' if self.active else 'inactive'})"


class Notification(TimeStampedModel):
    class Kind(models.TextChoices):
        PAYMENT = "payment", "Payment"
        WALLET = "wallet", "Wallet"
        TRANSFER = "transfer", "Transfer"
        SECURITY = "security", "Security"
        INFO = "info", "Information"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="notifications")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.INFO)
    title = models.CharField(max_length=80)
    body = models.CharField(max_length=240)
    data = models.JSONField(default=dict, blank=True)   # deep-link hints for the app
    # Which app's devices get the push ("" = every app the user has). Merchant alerts
    # go only to the Business app, so a shop owner's customer app stays personal.
    target_app = models.CharField(max_length=8, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    pushed_at = models.DateTimeField(null=True, blank=True)
    # Important alerts (cash-out approval, money out, security) also go by SMS when no
    # push reached a phone: the customer may have no data, or the app may be uninstalled.
    sms_fallback = models.BooleanField(default=False)
    sms_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_notification"
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self) -> str:
        return f"{self.user}: {self.title}"
