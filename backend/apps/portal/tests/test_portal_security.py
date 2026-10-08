"""
Merchant-portal 2FA (policy, enrolment, backup codes, staff reset) and portal rate
limiting (login per phone/IP, 2FA attempts, action throttles, trusted-proxy IP).
"""

import io

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding
from apps.merchants.models import ApiKey, MerchantMember
from apps.portal import ratelimit, security
from apps.portal.models import BackupCode, SecurityEvent

from .helpers import PASSWORD, current_token, login, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _licence(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def shop(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    m = onboarding.create_merchant(owner=owner, legal_name="Shop Ltd", business_type="registered")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return m


def _member(shop, phone, role):
    user = User.objects.create_user(phone=phone, full_name=role.title(), password=PASSWORD)
    MerchantMember.objects.create(merchant=shop, user=user, role=role)
    return user


def _enrol(client):
    """Walk the real enrolment screen: GET shows QR, POST the code. Returns backup codes."""
    page = client.get("/dashboard/2fa/")
    assert b"Set up two-factor" in page.content
    device = TOTPDevice.objects.get(user=page.wsgi_request.user, confirmed=False)
    r = client.post("/dashboard/2fa/", {"token": current_token(device)})
    assert r.status_code == 200 and b"Save your backup codes" in r.content
    return r.context["codes"]


# --- policy -------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["owner", "admin", "finance"])
def test_money_roles_must_enrol_before_using_the_portal(shop, role):
    user = shop.owner if role == "owner" else _member(shop, f"+23320000002{len(role)}", role)
    c = login(user.phone)
    for url in ("/dashboard/", "/dashboard/settlements/", "/dashboard/bulk/", "/dashboard/api-keys/"):
        r = c.get(url)
        assert r.status_code == 302 and r["Location"].endswith("/dashboard/2fa/"), url
    codes = _enrol(c)
    assert len(codes) == 10 and BackupCode.objects.filter(user=user).count() == 10
    assert c.get("/dashboard/").status_code == 200
    assert SecurityEvent.objects.filter(user=user, kind="twofa_enrolled").exists()


def test_cashier_optional_until_enrolled(shop):
    cashier = _member(shop, "+233200000030", "cashier")
    c = login(cashier.phone)
    assert c.get("/dashboard/").status_code == 200              # not required for cashiers
    assert b"Set up 2FA" in c.get("/dashboard/security/").content
    _enrol(c)                                                    # opts in…
    c2 = login(cashier.phone)                                    # …so new sessions need it
    assert c2.get("/dashboard/").status_code == 302


def test_backup_code_works_once(shop):
    c = login(shop.owner.phone)
    codes = _enrol(c)
    c.logout()

    c = login(shop.owner.phone)
    r = c.post("/dashboard/2fa/", {"token": codes[0].lower()})      # case/format tolerant
    assert r.status_code == 302 and c.get("/dashboard/").status_code == 200
    assert SecurityEvent.objects.filter(kind="backup_used", user=shop.owner).exists()
    c.logout()

    c = login(shop.owner.phone)
    c.post("/dashboard/2fa/", {"token": codes[0]})                   # already used
    assert c.get("/dashboard/").status_code == 302
    assert security.backup_codes_left(shop.owner) == 9


def test_backup_codes_are_stored_hashed(shop):
    codes = security.generate_backup_codes(shop.owner)
    stored = set(BackupCode.objects.values_list("code_hash", flat=True))
    assert not any(c.replace("-", "") in h.upper() for c in codes for h in stored)
    assert security.consume_backup_code(shop.owner, codes[3])
    other = _member(shop, "+233200000031", "finance")
    assert not security.consume_backup_code(other, codes[4])        # bound to the owner


def test_regenerating_codes_needs_a_current_code_and_kills_old_ones(shop):
    c = login(shop.owner.phone)
    old = _enrol(c)
    device = TOTPDevice.objects.get(user=shop.owner, confirmed=True)
    assert b"That code is not valid" in c.post(
        "/dashboard/security/", {"action": "regenerate", "token": "000000"}, follow=True).content
    device.last_t = -1
    device.save()
    r = c.post("/dashboard/security/", {"action": "regenerate", "token": current_token(device)})
    new = r.context["codes"]
    assert set(new).isdisjoint(old)
    assert not security.consume_backup_code(shop.owner, old[1])
    assert security.consume_backup_code(shop.owner, new[1])


def test_staff_reset_removes_2fa_and_is_audited(shop):
    c = login(shop.owner.phone)
    _enrol(c)
    staff = User.objects.create_user(phone="+233200000099", password=PASSWORD, user_type="staff")
    s = login_verified(staff.phone)
    page = s.get(f"/dashboard/admin/merchants/{shop.id}/")
    assert b"Reset 2FA" in page.content
    member = shop.members.get(user=shop.owner)
    s.post(f"/dashboard/admin/merchants/{shop.id}/", {"action": "reset_2fa", "member": str(member.pk)})
    assert not TOTPDevice.objects.filter(user=shop.owner).exists()
    assert not BackupCode.objects.filter(user=shop.owner).exists()
    ev = SecurityEvent.objects.get(kind="twofa_reset")
    assert ev.user == shop.owner and ev.actor == staff
    # The owner's existing session is no longer verified → back to enrolment.
    assert c.get("/dashboard/").status_code == 302


def test_merchant_cannot_reset_2fa(shop):
    c = login_verified(shop.owner.phone)
    member = shop.members.get(user=shop.owner)
    r = c.post(f"/dashboard/admin/merchants/{shop.id}/", {"action": "reset_2fa", "member": str(member.pk)})
    assert r.status_code == 403
    assert TOTPDevice.objects.filter(user=shop.owner).exists()


# --- 2FA brute force ----------------------------------------------------------------
def test_wrong_codes_lock_2fa_and_sign_out(shop):
    login_verified(shop.owner.phone)                       # enrolled
    c = login(shop.owner.phone)
    for _ in range(4):
        assert c.post("/dashboard/2fa/", {"token": "000000"}).status_code == 200
    r = c.post("/dashboard/2fa/", {"token": "000000"})
    assert r.status_code == 302 and r["Location"].endswith("/dashboard/login/")
    assert SecurityEvent.objects.filter(kind="twofa_locked", user=shop.owner).exists()

    # Even with the right password and right code, still locked for the window.
    c = login(shop.owner.phone)
    device = TOTPDevice.objects.get(user=shop.owner, confirmed=True)
    device.last_t = -1
    device.save()
    c.post("/dashboard/2fa/", {"token": current_token(device)})
    assert c.get("/dashboard/").status_code == 302


# --- login rate limiting --------------------------------------------------------------
def _post_login(client, phone, password, **extra):
    return client.post("/dashboard/login/", {"phone": phone, "password": password}, **extra)


def test_login_locks_phone_after_five_failures(shop, client):
    for _ in range(5):
        r = _post_login(client, shop.owner.phone, "wrong")
        assert r.status_code == 200 and b"Wrong phone or password" in r.content
    r = _post_login(client, shop.owner.phone, PASSWORD)              # right password, still locked
    assert r.status_code == 429 and b"Too many attempts" in r.content
    assert SecurityEvent.objects.filter(kind="login_locked", user=shop.owner).count() == 1
    # A different account from the same IP is unaffected (under the IP limit).
    other = _member(shop, "+233200000040", "cashier")
    assert _post_login(client, other.phone, PASSWORD).status_code == 302


def test_unknown_phone_gets_same_message_and_is_counted(client):
    for _ in range(5):
        r = _post_login(client, "+233209999999", "x")
        assert b"Wrong phone or password" in r.content
    assert _post_login(client, "+233209999999", "x").status_code == 429


def test_login_ip_limit_stops_credential_stuffing(settings, shop, client):
    settings.PORTAL_RATE_LIMITS = {"login_ip": (3, 900)}
    for i in range(3):
        _post_login(client, f"+23320100000{i}", "x")
    assert _post_login(client, shop.owner.phone, PASSWORD).status_code == 429


def test_success_resets_phone_counter(shop, client):
    for _ in range(4):
        _post_login(client, shop.owner.phone, "wrong")
    assert _post_login(client, shop.owner.phone, PASSWORD).status_code == 302
    client.logout()
    for _ in range(4):
        assert _post_login(client, shop.owner.phone, "wrong").status_code == 200


def test_client_ip_trusts_only_configured_proxies(settings):
    rf = RequestFactory()
    req = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="6.6.6.6, 41.66.1.2")
    settings.TRUSTED_PROXY_COUNT = 0
    assert ratelimit.client_ip(req) == "10.0.0.5"            # XFF ignored: client-controlled
    settings.TRUSTED_PROXY_COUNT = 1
    assert ratelimit.client_ip(req) == "41.66.1.2"           # what the ALB saw; 6.6.6.6 is forged
    req = rf.get("/", REMOTE_ADDR="10.0.0.5")
    assert ratelimit.client_ip(req) == "10.0.0.5"


def test_ip_limit_cannot_be_dodged_by_forging_xff(settings, shop, client):
    settings.TRUSTED_PROXY_COUNT = 1
    settings.PORTAL_RATE_LIMITS = {"login_ip": (3, 900)}
    for i in range(3):
        _post_login(client, f"+23320100000{i}", "x",
                    HTTP_X_FORWARDED_FOR=f"9.9.9.{i}, 41.66.1.2")      # rotating fake left part
    r = _post_login(client, shop.owner.phone, PASSWORD, HTTP_X_FORWARDED_FOR="1.1.1.1, 41.66.1.2")
    assert r.status_code == 429


def test_cache_keys_hold_no_phone_numbers(shop):
    ratelimit.hit("login_phone", shop.owner.phone)
    assert shop.owner.phone not in ratelimit._key("login_phone", shop.owner.phone)


# --- action throttles -----------------------------------------------------------------
def test_api_key_creation_is_throttled(settings, shop):
    settings.PORTAL_RATE_LIMITS = {"api_key_create": (2, 3600)}
    c = login_verified(shop.owner.phone)
    for _ in range(3):
        c.post("/dashboard/api-keys/", {"mode": "test"})
    assert ApiKey.objects.filter(merchant=shop).count() == 2


def test_bulk_upload_is_throttled(settings, shop):
    from apps.bulk.models import BulkPayout
    settings.PORTAL_RATE_LIMITS = {"bulk_upload": (1, 3600)}
    c = login_verified(shop.owner.phone)
    for i in range(2):
        f = io.BytesIO(f"name,phone,amount\nA,024123456{i},1.00\n".encode())
        f.name = f"p{i}.csv"
        r = c.post("/dashboard/bulk/", {"file": f}, follow=True)
    assert BulkPayout.objects.filter(merchant=shop).count() == 1
    assert b"Too many uploads" in r.content


def test_settlement_account_add_is_throttled(settings, shop):
    settings.PORTAL_RATE_LIMITS = {"settlement_account": (1, 3600)}
    c = login_verified(shop.owner.phone)
    for i in range(2):
        c.post("/dashboard/settlements/", {"add_account": "1", "kind": "momo", "provider": "mtn",
                                           "account_no": f"024000000{i}", "account_name": "Shop"})
    assert shop.settlement_accounts.count() == 1
