"""Push notifications: device registry, inbox, delivery after commit, dead tokens."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.notifications import services
from apps.notifications.models import Device, Notification
from apps.notifications.push.console import ConsolePushProvider
from apps.notifications.push.registry import reset_push_cache

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.PUSH_PROVIDER = "console"
    reset_push_cache()
    ConsolePushProvider.reset()
    yield
    reset_push_cache()


@pytest.fixture
def ama(db):
    return User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")


def test_notify_writes_inbox_and_pushes_after_commit(ama, django_capture_on_commit_callbacks):
    services.register_device(ama, platform="android", app="customer", token="tok-" + "a" * 40)
    with django_capture_on_commit_callbacks(execute=True):
        note = services.notify(ama, kind="wallet", title="Hello", body="World", data={"x": 1})
    assert Notification.objects.filter(user=ama, title="Hello").exists()
    assert len(ConsolePushProvider.sent) == 1
    assert ConsolePushProvider.sent[0]["data"]["x"] == "1"     # stringified (FCM data rule)
    note.refresh_from_db()
    assert note.pushed_at is not None


def test_dead_token_is_deactivated(ama, django_capture_on_commit_callbacks):
    services.register_device(ama, platform="ios", app="customer", token="dead-" + "b" * 40)
    with django_capture_on_commit_callbacks(execute=True):
        services.notify(ama, title="Hi", body="there")
    assert not Device.objects.get(user=ama).active
    assert ConsolePushProvider.sent == []


def test_token_moves_to_new_user(ama):
    other = User.objects.create_user(phone="+233200000001")
    token = "tok-" + "c" * 40
    services.register_device(other, platform="android", app="customer", token=token)
    services.register_device(ama, platform="android", app="customer", token=token)
    assert Device.objects.get(token=token).user == ama       # one token, one owner


def test_money_events_create_notifications(ama):
    """A wallet top-up and a P2P both leave inbox entries for the right people."""
    import json

    from apps.licensing.gate import _enabled_set
    from apps.payments import services as payment_services
    from apps.rails.mock import MockRail
    from apps.rails.registry import reset_rail_cache
    from apps.rails.types import Network, RailStatus
    from apps.wallet import services as wallet
    from django.conf import settings as dj
    dj.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    dj.RAIL_PROVIDER = "mock"
    _enabled_set.cache_clear()
    reset_rail_cache()
    MockRail.reset()
    kofi = User.objects.create_user(phone="+233209998888", full_name="Kofi Owusu")

    p = wallet.initiate_funding(user=ama, amount_minor=100_00, network=Network.MTN)
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    wallet.send_p2p(sender=ama, recipient_phone=kofi.phone, amount_minor=40_00)

    assert Notification.objects.filter(user=ama, title="Wallet topped up").exists()
    received = Notification.objects.get(user=kofi, title="Money received")
    assert "GH₵ 40.00" in received.body and "Ama M." in received.body


def test_device_and_inbox_api(ama):
    client = APIClient()
    client.force_authenticate(user=ama)
    r = client.post("/api/v1/devices", {"platform": "android", "app": "customer",
                                        "token": "tok-" + "d" * 40}, format="json")
    assert r.status_code == 201
    assert client.post("/api/v1/devices", {"platform": "android", "token": "short"},
                       format="json").status_code == 400

    note = services.notify(ama, title="T", body="B")
    inbox = client.get("/api/v1/notifications").json()
    assert inbox[0]["title"] == "T" and inbox[0]["read"] is False
    assert client.post(f"/api/v1/notifications/{note.id}/read").status_code == 200
    assert client.get("/api/v1/notifications").json()[0]["read"] is True

    other = User.objects.create_user(phone="+233200000002")
    client.force_authenticate(user=other)
    assert client.post(f"/api/v1/notifications/{note.id}/read").status_code == 404
    assert client.delete("/api/v1/devices/tok-" + "d" * 40).status_code == 204
    assert Device.objects.get(token="tok-" + "d" * 40).active   # not theirs to disable