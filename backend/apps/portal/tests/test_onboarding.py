"""
Bringing real people on board through the portals: staff "Add merchant" (with encrypted
KYB documents), SMS invites to set a password, forgot/change password, self-signup,
the merchant Team page, owner SMS on review decisions, and back-office "Add agent".
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts import otp
from apps.agents.models import Agent
from apps.common.models import AuditEvent
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding
from apps.merchants.models import Merchant, MerchantMember
from apps.notifications.sms.console import ConsoleSmsProvider
from apps.notifications.sms.registry import reset_sms_cache
from apps.portal import invites
from apps.portal.models import PortalInvite, SecurityEvent

from .helpers import PASSWORD, login, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db
NEW_PASSWORD = "Kente-Weaver-2026!"
PDF = b"%PDF-1.4\n" + b"x" * 2000
PNG = b"\x89PNG\r\n\x1a\n" + b"y" * 2000


@pytest.fixture(autouse=True)
def _env(settings, tmp_path):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.SMS_PROVIDER = "console"
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.FIELD_ENCRYPTION_KEY = "test-field-key"
    settings.MERCHANT_SELF_SIGNUP = True
    _enabled_set.cache_clear()
    reset_sms_cache()
    ConsoleSmsProvider.reset()
    cache.clear()
    yield
    _enabled_set.cache_clear()
    reset_sms_cache()
    cache.clear()


def _staff(phone, *groups):
    u = User.objects.create_user(phone=phone, full_name="Staff " + phone[-3:], password=PASSWORD, user_type="staff")
    for g in groups:
        u.groups.add(Group.objects.get(name=g))
    return u


@pytest.fixture
def ops(db):
    return _staff("+233200000201", "operations")


def _sms_to(phone):
    return [m for to, m in ConsoleSmsProvider.sent if to == phone]


def _link_token(message):
    return re.search(r"/dashboard/invite/([^/]+)/", message).group(1)


def _code(message):
    return re.search(r"\b(\d{6})\b", message).group(1)


def _add_merchant(client, phone="0244111222", **extra):
    data = {"owner_name": "Adwoa Mensah", "owner_phone": phone, "legal_name": "Adwoa Fabrics Ltd",
            "trading_name": "Adwoa Fabrics", "business_type": "registered", "category": "Textiles",
            "tin": "C0009998887", **extra}
    return client.post("/dashboard/admin/merchants/new/", data)


# --- staff: add merchant ----------------------------------------------------------------
def test_staff_adds_merchant_with_documents_and_owner_gets_invite(ops, settings):
    c = login_verified(ops.phone)
    assert c.get("/dashboard/admin/merchants/new/").status_code == 200
    r = _add_merchant(c, doc_owner_id=SimpleUploadedFile("id.png", PNG),
                      doc_registration=SimpleUploadedFile("reg.pdf", PDF))
    m = Merchant.objects.get(legal_name="Adwoa Fabrics Ltd")
    assert r.status_code == 302 and r["Location"].endswith(f"/admin/merchants/{m.pk}/")
    assert m.status == "submitted" and m.owner.phone == "+233244111222" and m.owner.full_name == "Adwoa Mensah"
    assert not m.owner.has_usable_password()                        # they set it themselves
    assert MerchantMember.objects.get(merchant=m, user=m.owner).role == "owner"
    # Documents: stored encrypted on disk, readable only through the staff download view.
    docs = {d.kind: d for d in m.documents.all()}
    assert set(docs) == {"owner_id", "registration"}
    on_disk = (Path(settings.MEDIA_ROOT) / docs["registration"].storage_key).read_bytes()
    assert b"%PDF" not in on_disk
    dl = c.get(f"/dashboard/admin/merchants/{m.pk}/documents/{docs['registration'].pk}/")
    assert dl.status_code == 200 and dl.content == PDF and dl["Content-Type"] == "application/pdf"
    assert AuditEvent.objects.filter(action="merchant.document_view").exists()
    assert AuditEvent.objects.filter(action="merchant.create").exists()
    # Invite SMS with a set-password link.
    (msg,) = _sms_to("+233244111222")
    assert "Adwoa Fabrics" in msg and "/dashboard/invite/" in msg and "expires in 3 days" in msg
    assert SecurityEvent.objects.filter(user=m.owner, kind="invite_sent").exists()


def test_staff_add_merchant_rejects_bad_files_and_taken_numbers(ops):
    c = login_verified(ops.phone)
    r = _add_merchant(c, doc_tin=SimpleUploadedFile("x.exe", b"MZ\x90\x00" * 100))
    assert r.status_code == 200 and b"PDF, JPEG or PNG" in r.content
    assert not Merchant.objects.exists() and not User.objects.filter(phone="+233244111222").exists()
    _add_merchant(c)
    r = _add_merchant(c, legal_name="Second Business Ltd")
    assert b"already linked to a business" in r.content
    r = _add_merchant(c, phone="12345")
    assert b"valid Ghana phone number" in r.content


def test_existing_customer_becomes_merchant_owner_keeping_their_account(ops):
    customer = User.objects.create_user(phone="+233244111222", full_name="Adwoa M")
    customer.set_pin("482915")
    customer.save()
    _add_merchant(login_verified(ops.phone))
    customer.refresh_from_db()
    assert Merchant.objects.get().owner == customer and customer.check_pin("482915")


def test_only_operations_or_compliance_staff_can_add_merchants():
    support = _staff("+233200000209", "support")
    assert login_verified(support.phone).get("/dashboard/admin/merchants/new/").status_code == 403


def test_documents_need_the_right_staff_role(ops):
    c = login_verified(ops.phone)
    _add_merchant(c, doc_owner_id=SimpleUploadedFile("id.png", PNG))
    m = Merchant.objects.get()
    doc = m.documents.get()
    support = _staff("+233200000209", "support")
    assert login_verified(support.phone).get(
        f"/dashboard/admin/merchants/{m.pk}/documents/{doc.pk}/").status_code == 403


# --- invites ------------------------------------------------------------------------------
def test_invite_link_sets_password_once_then_sign_in_works(ops):
    _add_merchant(login_verified(ops.phone))
    token = _link_token(_sms_to("+233244111222")[0])
    anon = Client()
    page = anon.get(f"/dashboard/invite/{token}/")
    assert page.status_code == 200 and b"Adwoa Fabrics" in page.content
    r = anon.post(f"/dashboard/invite/{token}/", {"password1": NEW_PASSWORD, "password2": "different"})
    assert b"don&#x27;t match" in r.content
    r = anon.post(f"/dashboard/invite/{token}/", {"password1": "1234567890", "password2": "1234567890"})
    assert r.status_code == 200 and not User.objects.get(phone="+233244111222").has_usable_password()
    r = anon.post(f"/dashboard/invite/{token}/", {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
    assert r.status_code == 302 and "/dashboard/login/" in r["Location"]
    assert Client().login(phone="+233244111222", password=NEW_PASSWORD)
    assert anon.get(f"/dashboard/invite/{token}/").status_code == 404           # one use only
    assert SecurityEvent.objects.filter(kind="password_set").exists()


def test_invite_expires_and_a_new_one_cancels_the_old(ops):
    _add_merchant(login_verified(ops.phone))
    owner = User.objects.get(phone="+233244111222")
    old = _link_token(_sms_to(owner.phone)[0])
    new = invites.create_invite(owner)
    assert Client().get(f"/dashboard/invite/{old}/").status_code == 404
    PortalInvite.objects.filter(used_at__isnull=True).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert Client().get(f"/dashboard/invite/{new}/").status_code == 404


def test_guessing_invite_tokens_is_rate_limited():
    c = Client()
    for _ in range(20):
        c.get("/dashboard/invite/not-a-real-token/")
    assert c.get("/dashboard/invite/not-a-real-token/").status_code == 429


def test_staff_can_resend_invite_from_review_page(ops):
    c = login_verified(ops.phone)
    _add_merchant(c)
    m = Merchant.objects.get()
    member = m.members.get()
    c.post(f"/dashboard/admin/merchants/{m.pk}/", {"action": "resend_invite", "member": member.pk})
    assert len(_sms_to("+233244111222")) == 2


# --- approval SMS -------------------------------------------------------------------------
def test_owner_is_texted_the_review_outcome(ops):
    c = login_verified(ops.phone)
    _add_merchant(c)
    m = Merchant.objects.get()
    c.post(f"/dashboard/admin/merchants/{m.pk}/", {"action": "begin_review"})
    c.post(f"/dashboard/admin/merchants/{m.pk}/", {"action": "approve", "risk_tier": "low"})
    m.refresh_from_db()
    assert m.status == "approved"
    assert "is approved" in _sms_to(m.owner.phone)[-1] and m.short_code in _sms_to(m.owner.phone)[-1]


# --- forgot / change password ---------------------------------------------------------------
@pytest.fixture
def owner(db):
    u = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    m = onboarding.create_merchant(owner=u, legal_name="Shop Ltd", business_type="registered")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return u


def test_forgot_password_by_sms_code(owner):
    c = Client()
    r = c.post("/dashboard/password/forgot/", {"phone": "0200000010"})
    assert r.status_code == 302 and r["Location"].endswith("/dashboard/password/reset/")
    code = _code(_sms_to(owner.phone)[-1])
    r = c.post("/dashboard/password/reset/", {"code": "000000" if code != "000000" else "111111",
                                              "password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
    assert b"not valid" in r.content
    r = c.post("/dashboard/password/reset/", {"code": code, "password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
    assert r.status_code == 302
    owner.refresh_from_db()
    assert owner.check_password(NEW_PASSWORD)
    assert SecurityEvent.objects.filter(user=owner, kind="password_reset").exists()


def test_forgot_password_gives_no_hint_about_unknown_numbers(owner):
    customer = User.objects.create_user(phone="+233244000777")
    for phone in ("0244000999", customer.phone):         # nobody / a customer without portal access
        r = Client().post("/dashboard/password/forgot/", {"phone": phone})
        assert r.status_code == 302 and r["Location"].endswith("/dashboard/password/reset/")
    assert ConsoleSmsProvider.sent == []


def test_an_app_sign_in_code_cannot_reset_a_portal_password(owner):
    c = Client()
    c.post("/dashboard/password/forgot/", {"phone": owner.phone})
    login_code = otp.request_otp(owner.phone)                # purpose "login"
    reset_code = _code(_sms_to(owner.phone)[-1])
    if login_code != reset_code:
        r = c.post("/dashboard/password/reset/", {"code": login_code, "password1": NEW_PASSWORD,
                                                  "password2": NEW_PASSWORD})
        assert b"not valid" in r.content


def test_change_password_on_security_page(owner):
    c = login_verified(owner.phone)
    r = c.post("/dashboard/security/", {"action": "change_password", "current": "wrong",
                                        "new1": NEW_PASSWORD, "new2": NEW_PASSWORD}, follow=True)
    assert b"current password is not correct" in r.content
    c.post("/dashboard/security/", {"action": "change_password", "current": PASSWORD,
                                    "new1": NEW_PASSWORD, "new2": NEW_PASSWORD})
    owner.refresh_from_db()
    assert owner.check_password(NEW_PASSWORD)
    assert c.get("/dashboard/").status_code == 200                       # still signed in here


def test_login_accepts_local_number_format(owner):
    r = Client().post("/dashboard/login/", {"phone": "020 000 0010", "password": PASSWORD})
    assert r.status_code == 302


# --- self-signup ------------------------------------------------------------------------------
SIGNUP = {"full_name": "Kwaku Ansah", "phone": "0277123456", "legal_name": "Kwaku Mobile Repairs",
          "trading_name": "Kwaku Mobile", "business_type": "sole_trader", "category": "Electronics"}


def test_self_signup_end_to_end(ops):
    c = Client()
    assert c.get("/dashboard/register/").status_code == 200
    r = c.post("/dashboard/register/", SIGNUP)
    assert r.status_code == 302 and r["Location"].endswith("/dashboard/register/verify/")
    code = _code(_sms_to("+233277123456")[-1])
    r = c.post("/dashboard/register/verify/", {"code": code, "password1": NEW_PASSWORD, "password2": NEW_PASSWORD,
                                               "doc_owner_id": SimpleUploadedFile("id.jpg", b"\xff\xd8\xff" + b"z" * 900)})
    assert r.status_code == 302 and r["Location"].endswith("/dashboard/")
    m = Merchant.objects.get(legal_name="Kwaku Mobile Repairs")
    assert m.status == "submitted" and m.documents.count() == 1 and m.owner.check_password(NEW_PASSWORD)
    home = c.get("/dashboard/")                              # signed in and 2FA-verified by the SMS code
    assert home.status_code == 200 and b"being reviewed" in home.content
    staff_queue = login_verified(ops.phone).get("/dashboard/admin/merchants/")
    assert b"Kwaku Mobile" in staff_queue.content


def test_self_signup_wrong_code_and_taken_number(owner):
    c = Client()
    c.post("/dashboard/register/", SIGNUP)
    code = _code(_sms_to("+233277123456")[-1])
    r = c.post("/dashboard/register/verify/", {"code": "000000" if code != "000000" else "111111",
                                               "password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
    assert b"not correct" in r.content and not Merchant.objects.filter(legal_name="Kwaku Mobile Repairs").exists()
    r = Client().post("/dashboard/register/", {**SIGNUP, "phone": owner.phone})
    assert b"already linked to a business" in r.content


def test_self_signup_can_be_switched_off(settings):
    settings.MERCHANT_SELF_SIGNUP = False
    assert Client().get("/dashboard/register/").status_code == 404


# --- team ------------------------------------------------------------------------------------
def test_owner_invites_cashier_who_sets_password_and_signs_in(owner):
    c = login_verified(owner.phone)
    assert c.get("/dashboard/team/").status_code == 200
    c.post("/dashboard/team/", {"action": "invite", "full_name": "Kwesi Cashier", "phone": "0244555666",
                                "role": "cashier"})
    member = MerchantMember.objects.get(user__phone="+233244555666")
    assert member.role == "cashier"
    token = _link_token(_sms_to("+233244555666")[-1])
    Client().post(f"/dashboard/invite/{token}/", {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
    cashier = login("+233244555666", NEW_PASSWORD)
    assert cashier.get("/dashboard/").status_code == 200          # cashiers don't need 2FA
    r = cashier.post("/dashboard/team/", {"action": "invite", "full_name": "X", "phone": "0244555777",
                                          "role": "cashier"}, follow=True)
    assert b"Only the owner or an admin" in r.content


def test_admins_cannot_manage_admins_and_nobody_touches_the_owner(owner):
    shop = owner.merchant_memberships.get().merchant
    admin_user = User.objects.create_user(phone="+233244555001", full_name="Admin", password=PASSWORD)
    MerchantMember.objects.create(merchant=shop, user=admin_user, role="admin")
    other_admin = User.objects.create_user(phone="+233244555002", full_name="Admin 2", password=PASSWORD)
    other = MerchantMember.objects.create(merchant=shop, user=other_admin, role="admin")
    c = login_verified(admin_user.phone)
    r = c.post("/dashboard/team/", {"action": "invite", "full_name": "A3", "phone": "0244555003", "role": "admin"},
               follow=True)
    assert b"Only the owner can add" in r.content
    r = c.post("/dashboard/team/", {"action": "remove", "member": other.pk}, follow=True)
    assert MerchantMember.objects.filter(pk=other.pk).exists()
    owner_member = shop.members.get(role="owner")
    r = c.post("/dashboard/team/", {"action": "remove", "member": owner_member.pk}, follow=True)
    assert b"owner can&#x27;t be changed" in r.content


def test_change_role_and_remove_member(owner):
    shop = owner.merchant_memberships.get().merchant
    u = User.objects.create_user(phone="+233244555010", full_name="Fin", password=PASSWORD)
    mem = MerchantMember.objects.create(merchant=shop, user=u, role="cashier")
    c = login_verified(owner.phone)
    c.post("/dashboard/team/", {"action": "role", "member": mem.pk, "role": "finance"})
    mem.refresh_from_db()
    assert mem.role == "finance"
    member_client = login_verified(u.phone)
    c.post("/dashboard/team/", {"action": "remove", "member": mem.pk})
    assert not MerchantMember.objects.filter(pk=mem.pk).exists()
    assert member_client.get("/dashboard/").status_code == 403           # access ends immediately
    assert AuditEvent.objects.filter(action="merchant.member_remove").exists()


# --- agents -----------------------------------------------------------------------------------
def test_staff_adds_a_brand_new_agent(ops):
    c = login_verified(ops.phone)
    r = c.post("/dashboard/admin/agents/", {"full_name": "Akosua Darko", "phone": "0244777888",
                                            "display_name": "Akosua's Kiosk", "location": "Kejetia"})
    agent = Agent.objects.get(user__phone="+233244777888")
    assert r.status_code == 302 and agent.status == "pending" and agent.user.full_name == "Akosua Darko"
    assert agent.user.user_type == "agent" and agent.location == "Kejetia"
    msg = _sms_to("+233244777888")[-1]
    assert "registered as a SokoPay agent" in msg and "Agent app" in msg
    assert AuditEvent.objects.filter(action="agent.register").exists()


def test_add_agent_uses_existing_account_and_refuses_staff(ops):
    existing = User.objects.create_user(phone="+233244777999", full_name="Kofi Existing")
    c = login_verified(ops.phone)
    c.post("/dashboard/admin/agents/", {"phone": "0244777999", "display_name": "Kofi Shop"})
    assert Agent.objects.get().user == existing
    r = c.post("/dashboard/admin/agents/", {"phone": ops.phone, "full_name": "x"}, follow=True)
    assert b"staff accounts can&#x27;t be agents" in r.content
    r = c.post("/dashboard/admin/agents/", {"phone": "0244777000"}, follow=True)
    assert b"Enter the agent&#x27;s full name" in r.content
