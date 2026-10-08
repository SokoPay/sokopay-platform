"""Important alerts fall back to SMS when no push reaches a phone (once, never twice)."""

import pytest
from django.contrib.auth import get_user_model

from apps.notifications import services
from apps.notifications.models import Device, Notification
from apps.notifications.sms.base import SmsResult

User = get_user_model()
pytestmark = pytest.mark.django_db


class _Spy:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, to, message):
        self.sent.append((to, message))
        return SmsResult(success=self.ok)


@pytest.fixture
def spy(monkeypatch):
    s = _Spy()
    monkeypatch.setattr("apps.notifications.sms.registry.get_sms_provider", lambda *a, **k: s)
    return s


def test_sms_sent_when_no_device_and_only_once(spy, django_capture_on_commit_callbacks):
    u = User.objects.create_user(phone="+233244058519")
    with django_capture_on_commit_callbacks(execute=True):
        n = services.notify(u, title="Approve cash-out?", body="Esi Kiosk wants to pay you GH₵ 50.00 cash.",
                            app="customer", sms=True)
    assert len(spy.sent) == 1 and spy.sent[0][0] == u.phone
    assert spy.sent[0][1].startswith("SokoPay: Approve cash-out?") and len(spy.sent[0][1]) <= 160
    services.deliver(Notification.objects.get(pk=n.pk))          # a retry
    assert len(spy.sent) == 1


def test_no_sms_for_ordinary_alerts_or_when_push_worked(spy, django_capture_on_commit_callbacks, settings):
    u = User.objects.create_user(phone="+233244058520")
    with django_capture_on_commit_callbacks(execute=True):
        services.notify(u, title="Payment successful", body="x", app="customer")
    assert spy.sent == []
    settings.PUSH_PROVIDER = "console"
    Device.objects.create(user=u, platform="android", app="customer", token="tok-1")
    with django_capture_on_commit_callbacks(execute=True):
        services.notify(u, title="Cash withdrawn", body="y", app="customer", sms=True)
    assert spy.sent == []


def test_failed_sms_can_be_retried(monkeypatch):
    u = User.objects.create_user(phone="+233244058521")
    bad = _Spy(ok=False)
    monkeypatch.setattr("apps.notifications.sms.registry.get_sms_provider", lambda *a, **k: bad)
    n = Notification.objects.create(user=u, title="PIN reset", body="z", sms_fallback=True)
    assert services.send_sms_fallback(n) is False
    n.refresh_from_db()
    assert n.sms_sent_at is None
