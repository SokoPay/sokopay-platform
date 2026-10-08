"""A message-queue outage after a payment commits must not turn into an error for the customer."""

import pytest
from django.contrib.auth import get_user_model

from apps.notifications import services
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def test_notify_survives_a_broker_outage(monkeypatch, django_capture_on_commit_callbacks):
    from apps.notifications import tasks

    def down(*a, **k):
        raise ConnectionError("broker unreachable")
    monkeypatch.setattr(tasks.push_notification, "delay", down)
    u = get_user_model().objects.create_user(phone="+233244058519")
    with django_capture_on_commit_callbacks(execute=True):
        services.notify(u, title="Money received", body="x", app="customer")
    assert Notification.objects.filter(user=u).count() == 1       # still in the inbox
