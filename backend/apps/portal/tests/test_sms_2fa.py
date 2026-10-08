"""
Portal 2FA by SMS (PORTAL_2FA_SMS) and the configurable OTP lifetime (OTP_TTL_SECONDS):
the 2FA page texts a code, it signs the user in once, resend is throttled, codes are
scoped to their purpose, and authenticator/backup codes keep working alongside.
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

from apps.accounts import otp
from apps.notifications.sms.console import ConsoleSmsProvider
from apps.notifications.sms.registry import reset_sms_cache
from apps.portal import views as portal_views
from apps.portal.models import SmsDevice

from .helpers import PASSWORD, current_token, login

User = get_user_model()
pytestmark = pytest.mark.django_db
TWOFA = "/dashboard/2fa/"


@pytest.fixture(autouse=True)
def _sms(settings):
    settings.PORTAL_2FA_SMS = True
    settings.SMS_PROVIDER = "console"
    settings.OTP_TTL_SECONDS = 600
    reset_sms_cache()
    ConsoleSmsProvider.reset()
    cache.clear()
    yield
    reset_sms_cache()
    cache.clear()


@pytest.fixture
def staff(db):
    return User.objects.create_user(phone="+233200000020", full_name="Staff", password=PASSWORD,
                                    user_type="staff")


def _last_code(phone):
    to, message = ConsoleSmsProvider.sent[-1]
    assert to == phone
    return re.search(r"\b(\d{6})\b", message).group(1), message


def test_2fa_page_texts_a_code_that_signs_the_user_in(staff):
    c = login(staff.phone)
    page = c.get(TWOFA)
    assert page.status_code == 200 and b"sent a 6-digit code by SMS" in page.content
    assert b"+233 \xe2\x80\xa2\xe2\x80\xa2\xe2\x80\xa2" in page.content            # number masked
    code, message = _last_code(staff.phone)
    assert "expires in 10 minutes" in message
    r = c.post(TWOFA, {"token": code})
    assert r.status_code == 302 and r["Location"].endswith("/dashboard/admin/")
    assert c.get("/dashboard/admin/").status_code == 200
    assert SmsDevice.objects.filter(user=staff, confirmed=True).count() == 1


def test_code_is_single_use_and_wrong_codes_are_rejected(staff):
    c = login(staff.phone)
    c.get(TWOFA)
    code, _ = _last_code(staff.phone)
    wrong = "000000" if code != "000000" else "111111"
    r = c.post(TWOFA, {"token": wrong})
    assert r.status_code == 200 and b"not valid" in r.content
    assert c.post(TWOFA, {"token": code}).status_code == 302
    c2 = login(staff.phone)                                   # a new session needs a new code
    c2.get(TWOFA)
    assert len(ConsoleSmsProvider.sent) == 2


def test_refreshing_the_page_does_not_send_another_sms(staff):
    c = login(staff.phone)
    c.get(TWOFA)
    c.get(TWOFA)
    assert len(ConsoleSmsProvider.sent) == 1


def test_resend_is_throttled_then_sends_a_new_code(staff, monkeypatch):
    c = login(staff.phone)
    c.get(TWOFA)
    r = c.post(TWOFA, {"action": "resend"}, follow=True)
    assert b"Please wait" in r.content and len(ConsoleSmsProvider.sent) == 1
    now = portal_views.time.time()
    monkeypatch.setattr(portal_views.time, "time", lambda: now + portal_views.SMS_RESEND_SECONDS + 1)
    r = c.post(TWOFA, {"action": "resend"}, follow=True)
    assert b"sent you a new code" in r.content and len(ConsoleSmsProvider.sent) == 2
    old_code = re.search(r"\b(\d{6})\b", ConsoleSmsProvider.sent[0][1]).group(1)
    new_code, _ = _last_code(staff.phone)
    if old_code != new_code:
        assert c.post(TWOFA, {"token": old_code}).status_code == 200    # replaced by the new one
    assert c.post(TWOFA, {"token": new_code}).status_code == 302


def test_app_sign_in_code_does_not_pass_portal_2fa(staff):
    code = otp.request_otp(staff.phone)                       # purpose "login" (mobile app)
    c = login(staff.phone)
    c.get(TWOFA)
    if code != _last_code(staff.phone)[0]:                    # (1-in-a-million clash aside)
        assert c.post(TWOFA, {"token": code}).status_code == 200


def test_authenticator_still_works_with_sms_on(staff):
    device = TOTPDevice.objects.create(user=staff, confirmed=True, name="app")
    c = login(staff.phone)
    page = c.get(TWOFA)
    assert b"authenticator app" in page.content
    assert c.post(TWOFA, {"token": current_token(device)}).status_code == 302


def test_user_can_choose_an_authenticator_app_instead(staff):
    c = login(staff.phone)
    page = c.get(TWOFA + "?setup=app")
    assert b"Set up two-factor" in page.content
    device = TOTPDevice.objects.get(user=staff, confirmed=False)
    r = c.post(TWOFA + "?setup=app", {"token": current_token(device)})
    assert r.status_code == 200 and b"Save your backup codes" in r.content


def test_sms_failure_is_explained(staff, monkeypatch):
    from apps.notifications.sms.base import SmsResult
    monkeypatch.setattr(ConsoleSmsProvider, "send", lambda self, to, msg: SmsResult(success=False))
    page = login(staff.phone).get(TWOFA)
    assert b"couldn&#x27;t send the SMS" in page.content


def test_off_by_default_keeps_authenticator_enrolment(staff, settings):
    settings.PORTAL_2FA_SMS = False
    page = login(staff.phone).get(TWOFA)
    assert b"Set up two-factor" in page.content and ConsoleSmsProvider.sent == []


# --- OTP lifetime ---------------------------------------------------------------------
def test_otp_lifetime_is_configurable_and_used_in_the_app_sms(settings):
    settings.OTP_TTL_SECONDS = 15 * 60
    r = APIClient().post("/api/v1/auth/otp/request", {"phone": "+233244000299"}, format="json")
    assert r.status_code == 200
    assert "expires in 15 minutes" in ConsoleSmsProvider.sent[-1][1]


def test_code_expires_after_the_lifetime(settings, monkeypatch):
    seen = {}
    real_set = cache.set

    def spy(key, value, timeout=None, *a, **kw):
        seen[key] = timeout
        return real_set(key, value, timeout, *a, **kw)

    monkeypatch.setattr(cache, "set", spy)
    settings.OTP_TTL_SECONDS = 900
    otp.request_otp("+233244000298", purpose="portal2fa")
    assert seen["otp:portal2fa:code:+233244000298"] == 900
