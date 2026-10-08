"""Arkesel SMS provider: request shape, sender ID, sandbox, and failure handling (no network)."""

import pytest
import requests

from apps.notifications.sms import get_sms_provider
from apps.notifications.sms import arkesel as arkesel_mod
from apps.notifications.sms.arkesel import ARKESEL_ENDPOINT, ArkeselSmsProvider
from apps.notifications.sms.registry import reset_sms_cache


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Calls(list):
    """Records requests.post calls; `respond` sets what the fake Arkesel returns."""

    response = _Resp(200, {"status": "success", "data": [{"recipient": "233244000201", "id": "abc-123"}]})

    def respond(self, resp):
        self.response = resp


@pytest.fixture
def calls(monkeypatch, settings):
    settings.ARKESEL_API_KEY = "test-key"
    settings.ARKESEL_SENDER_ID = "MySokoApp"
    settings.ARKESEL_SANDBOX = False
    seen = _Calls()

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        seen.append({"url": url, "json": json, "headers": headers, "timeout": timeout, **kw})
        return seen.response

    monkeypatch.setattr(arkesel_mod.requests, "post", fake_post)
    return seen


def test_registry_selects_arkesel(settings):
    settings.SMS_PROVIDER = "arkesel"
    reset_sms_cache()
    try:
        assert get_sms_provider().name == "arkesel"
    finally:
        reset_sms_cache()


def test_send_posts_documented_v2_shape(calls):
    result = ArkeselSmsProvider().send("+233244000201", "Your SokoPay code is 123456.")
    assert result.success and result.provider_ref == "abc-123"
    call = calls[0]
    assert call["url"] == ARKESEL_ENDPOINT == "https://sms.arkesel.com/api/v2/sms/send"
    assert call["headers"]["api-key"] == "test-key"
    assert call["json"] == {"sender": "MySokoApp", "message": "Your SokoPay code is 123456.",
                            "recipients": ["233244000201"]}            # E.164 without the "+"
    assert call["timeout"] and "verify" not in call                      # TLS verification left on


def test_sender_falls_back_to_sms_sender_id_and_is_capped(calls, settings):
    settings.ARKESEL_SENDER_ID = ""
    settings.SMS_SENDER_ID = "SokoPayGhanaLtd"
    ArkeselSmsProvider().send("+233244000201", "hi")
    assert calls[0]["json"]["sender"] == "SokoPayGhan"                  # Arkesel's 11-char limit


def test_sandbox_flag(calls, settings):
    settings.ARKESEL_SANDBOX = True
    result = ArkeselSmsProvider().send("+233244000201", "hi")
    assert calls[0]["json"]["sandbox"] is True and result.message == "sandbox"


def test_missing_key_fails_without_calling(calls, settings):
    settings.ARKESEL_API_KEY = ""
    result = ArkeselSmsProvider().send("+233244000201", "hi")
    assert not result.success and result.message == "sms_not_configured" and calls == []


@pytest.mark.parametrize("resp", [
    _Resp(200, {"status": "error", "message": "Insufficient balance"}),
    _Resp(401, {"message": "Invalid API key"}),
    _Resp(500, ValueError("not json")),
])
def test_provider_errors_return_failure(calls, resp, caplog):
    calls.respond(resp)
    result = ArkeselSmsProvider().send("+233244000201", "Your SokoPay code is 654321.")
    assert not result.success
    assert "654321" not in caplog.text and "test-key" not in caplog.text   # never log codes or keys


def test_network_error_returns_failure(monkeypatch, settings):
    settings.ARKESEL_API_KEY = "test-key"

    def boom(*a, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(arkesel_mod.requests, "post", boom)
    result = ArkeselSmsProvider().send("+233244000201", "hi")
    assert not result.success and result.message == "ConnectionError"
