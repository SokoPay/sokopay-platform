"""Consumer auth: OTP sign-in, PIN set/login with lockout, JWT, and protected access."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts import otp, services, tokens

User = get_user_model()
pytestmark = pytest.mark.django_db
PHONE = "+233244058519"


# --- services ---------------------------------------------------------------
def test_otp_sign_in_creates_user_and_returns_tokens():
    code = services.request_otp(PHONE)
    user, issued, is_new = services.verify_otp_and_login(PHONE, code)
    assert is_new is True
    assert user.phone == PHONE
    assert "access" in issued and "refresh" in issued
    # Access token decodes to this user.
    payload = tokens.decode(issued["access"], "access")
    assert payload["sub"] == str(user.id)


def test_otp_is_single_use_and_wrong_code_fails():
    code = services.request_otp(PHONE)
    with pytest.raises(services.AuthError):
        services.verify_otp_and_login(PHONE, "000000")   # wrong
    services.verify_otp_and_login(PHONE, code)            # correct consumes it
    with pytest.raises(otp.OtpError):
        services.verify_otp_and_login(PHONE, code)        # already used → no code


def test_pin_set_rejects_weak_and_accepts_strong():
    user, *_ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    with pytest.raises(services.AuthError):
        services.set_pin(user, "000000")
    with pytest.raises(services.AuthError):
        services.set_pin(user, "12345")       # too short
    services.set_pin(user, "428173")
    user.refresh_from_db()
    assert user.has_pin


def test_pin_login_and_lockout():
    user, *_ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    services.set_pin(user, "428173")

    assert "access" in services.login_with_pin(PHONE, "428173")

    for _ in range(5):
        with pytest.raises(services.AuthError):
            services.login_with_pin(PHONE, "999999")
    # Now locked, even the correct PIN is refused until the lockout passes.
    with pytest.raises(services.AuthError):
        services.login_with_pin(PHONE, "428173")


def test_refresh_mints_new_access_and_retires_old_refresh():
    user, issued, _ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    refreshed = services.refresh(issued["refresh"])
    assert "access" in refreshed
    assert tokens.decode(refreshed["access"], "access")["sub"] == str(user.id)
    # Rotation: the refresh token that was just used is dead (a stolen copy is useless).
    with pytest.raises(services.AuthError):
        services.refresh(issued["refresh"])
    assert "access" in services.refresh(refreshed["refresh"])


def test_logout_revokes_access_and_refresh():
    user, issued, _ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued['access']}")
    assert client.get("/api/v1/auth/me").status_code == 200

    r = client.post("/api/v1/auth/logout", {"refresh": issued["refresh"]}, format="json")
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401       # access dead immediately
    with pytest.raises(services.AuthError):
        services.refresh(issued["refresh"])                        # refresh dead too


def test_logout_cannot_revoke_someone_elses_refresh_token():
    me, mine, _ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    other = "+233200000099"
    _, theirs, _ = services.verify_otp_and_login(other, services.request_otp(other))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {mine['access']}")
    client.post("/api/v1/auth/logout", {"refresh": theirs["refresh"]}, format="json")
    assert "access" in services.refresh(theirs["refresh"])         # untouched


def test_logout_all_and_pin_change_end_other_sessions():
    user, phone_a, _ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    services.set_pin(user, "428173")
    phone_b = services.login_with_pin(PHONE, "428173")

    a, b = APIClient(), APIClient()
    a.credentials(HTTP_AUTHORIZATION=f"Bearer {phone_a['access']}")
    b.credentials(HTTP_AUTHORIZATION=f"Bearer {phone_b['access']}")
    assert a.get("/api/v1/auth/me").status_code == b.get("/api/v1/auth/me").status_code == 200

    # Device B changes the PIN → device A is signed out, B gets fresh tokens and stays in.
    r = b.post("/api/v1/auth/pin/change", {"current_pin": "428173", "new_pin": "917346"}, format="json")
    assert r.status_code == 200 and "tokens" in r.json()
    assert a.get("/api/v1/auth/me").status_code == 401
    assert b.get("/api/v1/auth/me").status_code == 401             # B's *old* token is gone too
    b.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['tokens']['access']}")
    assert b.get("/api/v1/auth/me").status_code == 200

    assert b.post("/api/v1/auth/logout-all").status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401
    with pytest.raises(services.AuthError):
        services.refresh(r.json()["tokens"]["refresh"])


def test_purge_drops_only_expired_denylist_rows():
    from apps.accounts.models import RevokedToken
    user, issued, _ = services.verify_otp_and_login(PHONE, services.request_otp(PHONE))
    services.logout(user, tokens.decode(issued["access"], "access"), issued["refresh"])
    assert RevokedToken.objects.count() == 2
    assert tokens.purge_expired() == 0                              # neither has expired yet
    RevokedToken.objects.filter(token_type="access").update(
        expires_at=RevokedToken.objects.get(token_type="access").expires_at.replace(year=2000))
    assert tokens.purge_expired() == 1
    assert RevokedToken.objects.filter(token_type="refresh").exists()


# --- API + JWT-protected access ---------------------------------------------
def test_full_api_flow_and_protected_endpoint():
    client = APIClient()
    assert client.post("/api/v1/auth/otp/request", {"phone": PHONE}, format="json").status_code == 200

    # We can't read the SMS in a test, so fetch the code via the service cache path.
    code = otp.request_otp(PHONE)  # issues a fresh code we know
    verify = client.post("/api/v1/auth/otp/verify",
                         {"phone": PHONE, "code": code}, format="json")
    assert verify.status_code == 200, verify.content
    access = verify.json()["tokens"]["access"]

    # Protected endpoint rejects without a token...
    assert client.get("/api/v1/auth/me").status_code in (401, 403)
    # ...and works with the Bearer access token.
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["phone"] == PHONE
