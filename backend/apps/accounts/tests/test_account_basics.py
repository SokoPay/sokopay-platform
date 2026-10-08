"""
Account basics + SIM-swap protection: OTP alone can't open an existing account, PIN
change needs the current PIN, forgot-PIN needs OTP (+ Ghana Card if verified) and caps
sends for 24 h, profile editing, account closure and anonymisation.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import otp, services
from apps.kyc.exceptions import LimitExceeded
from apps.kyc.models import KycProfile
from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry

User = get_user_model()
pytestmark = pytest.mark.django_db
PHONE = "+233244058519"
PIN = "428173"


@pytest.fixture
def ama(db):
    u = User.objects.create_user(phone=PHONE, full_name="Ama Mensah")
    u.set_pin(PIN)
    u.save()
    return u


def _authed(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def test_sms_code_alone_does_not_open_an_existing_account(ama):
    c = APIClient()
    r = c.post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": otp.request_otp(PHONE)}, format="json")
    assert r.status_code == 200 and r.json()["next"] == "pin_login" and r.json()["tokens"] is None
    # New numbers still get a session so they can create a PIN.
    new = "+233200000123"
    r = c.post("/api/v1/auth/otp/verify", {"phone": new, "code": otp.request_otp(new)}, format="json")
    assert r.json()["next"] == "set_pin" and r.json()["tokens"]["access"]


def test_first_pin_only_via_set_and_change_needs_current(ama):
    c = _authed(ama)
    assert c.post("/api/v1/auth/pin/set", {"pin": "917346"}, format="json").status_code == 400
    assert c.post("/api/v1/auth/pin/change", {"current_pin": "000001", "new_pin": "917346"},
                  format="json").status_code == 400
    r = c.post("/api/v1/auth/pin/change", {"current_pin": PIN, "new_pin": "917346"}, format="json")
    assert r.status_code == 200 and r.json()["tokens"]["access"]
    ama.refresh_from_db()
    assert ama.check_pin("917346")


def test_forgot_pin_without_kyc(ama):
    c = APIClient()
    assert c.post("/api/v1/auth/pin/forgot", {"phone": PHONE}, format="json").json() == {"sent": True}
    # Unknown numbers get the identical answer (no enumeration).
    assert c.post("/api/v1/auth/pin/forgot", {"phone": "+233200009999"}, format="json").json() == {"sent": True}
    code = otp.request_otp(PHONE)
    r = c.post("/api/v1/auth/pin/reset", {"phone": PHONE, "code": code, "new_pin": "917346"}, format="json")
    assert r.status_code == 200 and r.json()["tokens"]["access"]
    ama.refresh_from_db()
    assert ama.check_pin("917346") and ama.pin_reset_at is not None


def test_forgot_pin_needs_ghana_card_once_verified(ama):
    from apps.common.encryption import lookup_hash
    KycProfile.objects.create(user=ama, tier=1, ghana_card_number="GHA-123456789-1",
                              ghana_card_hash=lookup_hash("GHA-123456789-1"))
    c = APIClient()
    for card, ok in (("", False), ("GHA-999999999-9", False), ("gha-123456789-1", True)):
        r = c.post("/api/v1/auth/pin/reset", {"phone": PHONE, "code": otp.request_otp(PHONE),
                                              "new_pin": "917346", "ghana_card": card}, format="json")
        assert (r.status_code == 200) is ok, (card, r.content)


def test_send_cap_for_24h_after_reset(ama):
    from apps.kyc import limits
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 3_000_00),
                        credit(accounts.customer_wallet(str(ama.id)), 3_000_00)])
    services.reset_pin(phone=PHONE, code=otp.request_otp(PHONE), new_pin="917346")
    ama.refresh_from_db()
    limits.check_debit(ama, 400_00)
    with pytest.raises(LimitExceeded, match="after a PIN reset"):
        limits.check_debit(ama, 600_00)
    User.objects.filter(pk=ama.pk).update(pin_reset_at=timezone.now() - timedelta(hours=25))
    ama.refresh_from_db()
    limits.check_debit(ama, 600_00)          # cooling-off over


def test_profile_edit_and_name_lock(ama):
    c = _authed(ama)
    r = c.patch("/api/v1/auth/profile", {"full_name": "Ama Serwaa Mensah", "email": "ama@example.com"},
                format="json")
    assert r.status_code == 200 and r.json()["full_name"] == "Ama Serwaa Mensah" and not r.json()["name_locked"]
    assert c.patch("/api/v1/auth/profile", {"email": "nope"}, format="json").status_code == 400
    KycProfile.objects.create(user=ama, tier=1, ghana_card_hash="h" * 64, verified_name="AMA SERWAA MENSAH")
    r = c.patch("/api/v1/auth/profile", {"full_name": "Someone Else"}, format="json")
    assert r.status_code == 400 and "Ghana Card" in r.json()["error"]


def test_close_account_rules_and_retention(ama):
    c = _authed(ama)
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 10_00),
                        credit(accounts.customer_wallet(str(ama.id)), 10_00)])
    r = c.post("/api/v1/auth/close", {"pin": PIN}, format="json")
    assert r.status_code == 400 and "still has money" in r.json()["error"]
    post_entry("drain", [debit(accounts.customer_wallet(str(ama.id)), 10_00),
                         credit(accounts.partner_clearing("mock"), 10_00)])
    KycProfile.objects.create(user=ama, frozen=True)
    assert c.post("/api/v1/auth/close", {"pin": PIN}, format="json").status_code == 400   # held
    KycProfile.objects.filter(user=ama).update(frozen=False)
    assert c.post("/api/v1/auth/close", {"pin": "000001"}, format="json").status_code == 400
    assert c.post("/api/v1/auth/close", {"pin": PIN, "reason": "moving abroad"}, format="json").status_code == 200
    ama.refresh_from_db()
    assert not ama.is_active and ama.closed_at and ama.closed_reason == "moving abroad"
    # Can't sign back in with the number while records are retained.
    r = APIClient().post("/api/v1/auth/otp/verify", {"phone": PHONE, "code": otp.request_otp(PHONE)}, format="json")
    assert r.status_code == 400 and "closed" in r.json()["error"]
    # Anonymised after the retention period; the number becomes free.
    User.objects.filter(pk=ama.pk).update(closed_at=timezone.now() - timedelta(days=365 * 5 + 1))
    assert services.anonymise_closed_accounts() == 1
    ama.refresh_from_db()
    assert ama.full_name == "" and ama.phone != PHONE and ama.anonymised_at
