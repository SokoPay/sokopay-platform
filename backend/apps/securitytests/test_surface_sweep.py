"""
Attack-surface sweep: every /api/ route, every method, as an anonymous caller, with forged
tokens, with another role's token, and with junk input. Nothing may crash (5xx), and only
the routes below may answer an anonymous caller.
"""

import json
import re
import uuid

import jwt
import pytest
from django.conf import settings
from django.urls import get_resolver

from apps.accounts import tokens

from .conftest import client_for

pytestmark = pytest.mark.django_db

# Routes an anonymous caller may reach (each protected another way, or deliberately public).
PUBLIC = {
    "api/v1/auth/otp/request", "api/v1/auth/otp/verify", "api/v1/auth/pin/forgot", "api/v1/auth/pin/reset",
    "api/v1/auth/login", "api/v1/auth/refresh",
    "api/v1/rails/<str:provider>/webhook",          # HMAC signature
    "api/v1/remittance/<str:partner>/webhook",      # HMAC signature
    "api/v1/ussd/callback",                         # shared secret from the USSD gateway
    "api/v1/statements/download",                   # single-use signed link
}
METHODS = ("get", "post", "put", "patch", "delete")
JUNK = [
    {},
    {"amount": "-100", "recipient": "+233244000202", "pin": "000000"},
    {"amount": "1e309", "phone": "x" * 5000},
    {"amount": "NaN", "code": "' OR 1=1 --", "reference": "../../etc/passwd"},
    {"amount": 10 ** 30, "network": None, "account": {"$ne": 1}},
]


def _routes():
    def walk(patterns, prefix=""):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            else:
                yield prefix + str(p.pattern)
    return sorted(r for r in walk(get_resolver().url_patterns) if r.startswith("api/v1/"))


def _fill(route: str) -> str:
    path = re.sub(r"<uuid:\w+>", str(uuid.uuid4()), route)
    path = re.sub(r"<int:\w+>", "999999", path)
    path = re.sub(r"<str:decision>", "approve", path)
    path = re.sub(r"<str:provider>|<str:partner>", "mock", path)
    path = re.sub(r"<str:\w+>", "SP-NOTREAL00", path)
    return "/" + path


ROUTES = _routes()


def call(client, method, path, payload=None):
    if method in ("get", "delete"):
        return getattr(client, method)(path)
    return getattr(client, method)(path, data=json.dumps(payload or {}), content_type="application/json")


@pytest.mark.parametrize("route", ROUTES)
def test_anonymous_callers_are_refused_and_nothing_crashes(route, anon):
    for method in METHODS:
        resp = call(anon, method, _fill(route), JUNK[1])
        assert resp.status_code < 500, (method, route, resp.status_code)
        if route not in PUBLIC and resp.status_code not in (405,):
            assert resp.status_code in (401, 403), (method, route, resp.status_code, resp.content[:200])


def _forged(user) -> list[str]:
    now = 2_000_000_000
    base = {"sub": str(user.id), "type": "access", "iat": 1, "exp": now, "jti": "x", "gen": 0}
    return [
        jwt.encode(base, "not-the-real-key", algorithm="HS256"),                  # wrong key
        jwt.encode(base, None, algorithm="none"),                                  # alg=none
        jwt.encode({**base, "type": "refresh"}, settings.SECRET_KEY, algorithm="HS256"),  # refresh as access
        jwt.encode({**base, "exp": 1000}, settings.SECRET_KEY, algorithm="HS256"),  # expired
        jwt.encode({**base, "gen": 99}, settings.SECRET_KEY, algorithm="HS256"),    # signed-out generation
        jwt.encode({k: v for k, v in base.items() if k != "jti"}, settings.SECRET_KEY, algorithm="HS256"),
        "Bearer", "a.b.c", "",
    ]


def test_forged_or_stale_tokens_never_authenticate(kofi):
    from rest_framework.test import APIClient
    for token in _forged(kofi):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        for path in ("/api/v1/wallet", "/api/v1/auth/me", "/api/v1/activity"):
            assert c.get(path).status_code in (401, 403), (token[:30], path)


def test_tokens_die_on_logout_and_logout_all(kofi):
    pair = tokens.issue_tokens(kofi)
    from rest_framework.test import APIClient
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
    assert c.get("/api/v1/wallet").status_code == 200
    c.post("/api/v1/auth/logout", {"refresh": pair["refresh"]}, format="json")
    assert c.get("/api/v1/wallet").status_code == 401
    assert APIClient().post("/api/v1/auth/refresh", {"refresh": pair["refresh"]}, format="json").status_code in (400, 401)


@pytest.mark.parametrize("payload", JUNK)
def test_junk_input_never_crashes_signed_in_routes(payload, kofi):
    c = client_for(kofi)
    for route in ROUTES:
        if "webhook" in route or "ussd" in route:
            continue
        for method in ("post", "patch"):
            resp = call(c, method, _fill(route), payload)
            assert resp.status_code < 500, (method, route, payload, resp.content[:300])


def test_customer_token_cannot_use_agent_or_merchant_routes(kofi):
    c = client_for(kofi)
    for route in ROUTES:
        if route.startswith(("api/v1/agent/", "api/v1/merchant-app/", "api/v1/merchant/")):
            for method in ("get", "post"):
                resp = call(c, method, _fill(route), {})
                assert resp.status_code in (401, 403, 404, 405), (method, route, resp.status_code)
