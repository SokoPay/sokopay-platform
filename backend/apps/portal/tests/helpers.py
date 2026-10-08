"""Test helpers: sign a portal user in, including 2FA, through the real views."""

from django.contrib.auth import get_user_model
from django.test import Client
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

PASSWORD = "portal-pass-123"


def current_token(device: TOTPDevice) -> str:
    value = totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return str(value).zfill(device.digits)


def login(phone: str, password: str = PASSWORD) -> Client:
    c = Client()
    assert c.login(phone=phone, password=password)
    return c


def login_verified(phone: str, password: str = PASSWORD) -> Client:
    """Password + 2FA. Enrols a confirmed authenticator if the user has none."""
    c = login(phone, password)
    user = get_user_model().objects.get(phone=phone)
    device = (TOTPDevice.objects.filter(user=user, confirmed=True).first()
              or TOTPDevice.objects.create(user=user, confirmed=True, name="test"))
    device.last_t = -1                       # allow a second sign-in in the same 30s step
    device.throttling_failure_count = 0
    device.save()
    r = c.post("/dashboard/2fa/", {"token": current_token(device)})
    assert r.status_code == 302, r.content
    return c
