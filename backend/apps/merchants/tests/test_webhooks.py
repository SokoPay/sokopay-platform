"""Outbound merchant webhooks: URL safety (SSRF), signing, retries, auto-disable, events."""

import hashlib
import hmac
import json
import socket

import pytest
from django.contrib.auth import get_user_model

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding, qr, webhooks
from apps.merchants.models import WebhookDelivery, WebhookEndpoint
from apps.wallet import merchant_pay

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.QR_BASE_URL = "https://pay.test"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


def _dns(monkeypatch, mapping):
    def fake(host, port, *a, **kw):
        if host not in mapping:
            raise socket.gaierror("nope")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def shop(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner")
    m = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd",
                                   business_type="registered", trading_name="Ama Stores")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return m


@pytest.mark.parametrize("url,ips", [
    ("http://shop.example/hook", ["93.184.216.34"]),            # not https
    ("https://internal.example/hook", ["10.0.0.5"]),             # private
    ("https://meta.example/hook", ["169.254.169.254"]),          # cloud metadata
    ("https://local.example/hook", ["127.0.0.1"]),               # loopback
    ("https://mixed.example/hook", ["93.184.216.34", "192.168.1.9"]),  # one bad address is enough
    ("https://v6.example/hook", ["::ffff:10.1.2.3"]),            # IPv4-mapped private
    ("https://user:pw@shop.example/hook", ["93.184.216.34"]),    # credentials in URL
    ("https://nowhere.example/hook", None),                      # DNS fails
])
def test_unsafe_urls_are_refused(monkeypatch, url, ips):
    host = url.split("//")[1].split("/")[0].split("@")[-1]
    _dns(monkeypatch, {host: ips} if ips else {})
    with pytest.raises(webhooks.WebhookUrlError):
        webhooks.check_url(url)


def test_delivery_is_signed_pinned_and_marked_delivered(shop, monkeypatch):
    _dns(monkeypatch, {"shop.example": ["93.184.216.34"]})
    ep = webhooks.add_endpoint(merchant=shop, url="https://shop.example/hook?x=1", events=[],
                               created_by=shop.owner)
    assert ep.secret.startswith("whsec_")
    assert WebhookEndpoint.objects.get(pk=ep.pk).secret == ep.secret     # decrypts at read
    seen = {}

    def fake_post(url, hostname, port, ip, body, headers):
        seen.update(ip=ip, hostname=hostname, body=body, headers=headers)
        return 200
    monkeypatch.setattr(webhooks, "_post", fake_post)
    n = webhooks.emit(shop, "settlement.paid", {"id": "s1"})
    assert n == 1
    d = WebhookDelivery.objects.get()
    webhooks.deliver(d.id)
    d.refresh_from_db()
    assert d.status == "succeeded" and d.attempts == 1
    assert seen["ip"] == "93.184.216.34" and seen["hostname"] == "shop.example"
    t, v1 = (part.split("=", 1)[1] for part in seen["headers"]["SokoPay-Signature"].split(","))
    expected = hmac.new(ep.secret.encode(), f"{t}.".encode() + seen["body"], hashlib.sha256).hexdigest()
    assert hmac.compare_digest(v1, expected)
    assert json.loads(seen["body"])["type"] == "settlement.paid"
    webhooks.deliver(d.id)                                   # already delivered: not re-sent
    assert WebhookDelivery.objects.get(pk=d.pk).attempts == 1


def test_failures_back_off_then_abandon_and_endpoint_switches_off(shop, monkeypatch):
    _dns(monkeypatch, {"shop.example": ["93.184.216.34"]})
    ep = webhooks.add_endpoint(merchant=shop, url="https://shop.example/hook", events=["refund.succeeded"],
                               created_by=shop.owner)
    assert webhooks.emit(shop, "payment.succeeded", {}) == 0    # not subscribed
    monkeypatch.setattr(webhooks, "_post", lambda *a: 500)
    webhooks.emit(shop, "refund.succeeded", {"id": "r"})
    d = WebhookDelivery.objects.get()
    for _ in range(len(webhooks.BACKOFF) + 1):
        webhooks.deliver(d.id)
    d.refresh_from_db()
    assert d.status == "abandoned" and d.last_status_code == 500
    # DNS later points inside our network: refused at delivery time too (rebinding).
    _dns(monkeypatch, {"shop.example": ["10.0.0.7"]})
    webhooks.emit(shop, "refund.succeeded", {"id": "r2"})
    d2 = WebhookDelivery.objects.exclude(pk=d.pk).get()
    webhooks.deliver(d2.id)
    d2.refresh_from_db()
    assert d2.status == "failed" and "public" in d2.last_error
    ep.refresh_from_db()
    ep.consecutive_failures = webhooks.DISABLE_AFTER - 1
    ep.save()
    WebhookDelivery.objects.filter(pk=d2.pk).update(next_attempt_at=None)
    webhooks.emit(shop, "refund.succeeded", {"id": "r3"})
    webhooks.retry_due()
    ep.refresh_from_db()
    assert not ep.active and "Switched off" in ep.disabled_reason


def test_wallet_payment_emits_payment_succeeded(shop, monkeypatch, django_capture_on_commit_callbacks):
    _dns(monkeypatch, {"shop.example": ["93.184.216.34"]})
    webhooks.add_endpoint(merchant=shop, url="https://shop.example/hook", events=["payment.succeeded"],
                          created_by=shop.owner)
    sent = []
    monkeypatch.setattr(webhooks, "_post", lambda url, h, port, ip, body, headers: sent.append(body) or 204)
    user = User.objects.create_user(phone="+233244058519")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(user.id)), 100_00)])
    with django_capture_on_commit_callbacks(execute=True):
        p = merchant_pay.pay(user=user, code=qr.static_payload(shop), amount_minor=40_00)
    assert len(sent) == 1
    data = json.loads(sent[0])["data"]
    assert data["reference"] == p.reference and data["amount_minor"] == 40_00
    assert "+233244058519" not in sent[0].decode()                 # payer is masked


def test_endpoint_limit_and_event_filtering(shop, monkeypatch):
    _dns(monkeypatch, {"shop.example": ["93.184.216.34"]})
    for i in range(webhooks.MAX_ENDPOINTS):
        webhooks.add_endpoint(merchant=shop, url=f"https://shop.example/h{i}", events=["bogus", "refund.failed"],
                              created_by=shop.owner)
    assert WebhookEndpoint.objects.first().events == ["refund.failed"]
    with pytest.raises(webhooks.WebhookUrlError, match="at most"):
        webhooks.add_endpoint(merchant=shop, url="https://shop.example/h9", events=[], created_by=shop.owner)
