"""
Back-office command centre: live overview, users (flag / hold / disable), transactions
(with CSV export), payments & settlement, compliance & risk, and who may see what.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache

from apps.common.models import AuditEvent
from apps.compliance.models import Alert
from apps.kyc.models import KycProfile
from apps.licensing.gate import _enabled_set
from apps.payments.models import Payment

from .helpers import PASSWORD, login, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.AI_PROVIDER = "off"
    _enabled_set.cache_clear()
    cache.clear()
    yield
    _enabled_set.cache_clear()
    cache.clear()


def _staff(phone, *groups):
    u = User.objects.create_user(phone=phone, full_name=f"Staff {phone[-3:]}", password=PASSWORD, user_type="staff")
    for g in groups:
        u.groups.add(Group.objects.get(name=g))
    return u


@pytest.fixture
def kofi(db):
    u = User.objects.create_user(phone="+233244000201", full_name="Kofi Asante")
    Payment.objects.create(reference="SP-CC0000001", purpose="bill", status="succeeded", user=u,
                           amount_minor=50_00, total_minor=50_00, network="mtn", rail="mock")
    Payment.objects.create(reference="SP-CC0000002", purpose="airtime", status="failed", user=u,
                           amount_minor=5_00, total_minor=5_00, network="mtn", rail="mock")
    return u


PAGES = {
    "users": "/dashboard/admin/users/",
    "transactions": "/dashboard/admin/transactions/",
    "payments": "/dashboard/admin/payments/",
    "risk": "/dashboard/admin/risk/",
    "insights": "/dashboard/admin/insights/",
}
ACCESS = {   # role → pages it may open
    "support": {"users", "transactions"},
    "operations": {"users", "transactions", "payments", "insights"},
    "finance": {"transactions", "payments", "insights"},
    "compliance": {"users", "transactions", "risk", "insights"},
}


@pytest.mark.parametrize("role", list(ACCESS))
def test_each_role_sees_only_its_sections(role, kofi):
    c = login_verified(_staff("+233200000250", role).phone)
    assert c.get("/dashboard/admin/").status_code == 200          # overview: every member of staff
    for name, url in PAGES.items():
        expected = 200 if name in ACCESS[role] else 403
        assert c.get(url).status_code == expected, (role, name)


def test_customers_and_merchants_cannot_open_the_back_office(kofi):
    kofi.set_password(PASSWORD)
    kofi.save()
    c = login(kofi.phone)                    # a customer with a password still has no back-office access
    for url in ["/dashboard/admin/", *PAGES.values()]:
        assert c.get(url).status_code in (302, 403), url


def test_overview_shows_live_numbers_and_refreshes(kofi):
    c = login_verified(_staff("+233200000251", "operations").phone)
    page = c.get("/dashboard/admin/")
    assert b"Total users" in page.content and b"Completed today" in page.content
    assert b"hx-trigger=\"every 15s\"" in page.content
    live = c.get("/dashboard/admin/live/")
    assert live.status_code == 200 and b"GH\xe2\x82\xb5 50.00" in live.content and b"50.0%" in live.content


def test_user_list_masks_phones_and_profile_view_is_audited(kofi):
    staff = _staff("+233200000252", "support")
    c = login_verified(staff.phone)
    listing = c.get("/dashboard/admin/users/?q=0244000201")
    assert b"Kofi Asante" in listing.content and b"+233244000201" not in listing.content
    detail = c.get(f"/dashboard/admin/users/{kofi.pk}/")
    assert detail.status_code == 200 and b"+233244000201" in detail.content
    assert AuditEvent.objects.filter(action="user.view", actor=staff).exists()
    assert c.get(f"/dashboard/admin/users/{staff.pk}/").status_code == 403      # staff aren't managed here


def test_flag_opens_a_compliance_case(kofi):
    c = login_verified(_staff("+233200000253", "support").phone)
    r = c.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "flag", "severity": "high", "reason": ""}, follow=True)
    assert b"A reason is required" in r.content and not Alert.objects.exists()
    c.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "flag", "severity": "high", "reason": "Reported by bank"})
    alert = Alert.objects.get(user=kofi)
    assert alert.rule == "MANUAL_REVIEW" and alert.severity == "high" and "Reported by bank" in alert.title
    assert AuditEvent.objects.filter(action="user.flag").exists()
    assert b"Flagged" in c.get("/dashboard/admin/users/?state=flagged").content


def test_only_compliance_can_hold_a_wallet(kofi):
    support = login_verified(_staff("+233200000254", "support").phone)
    assert support.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "hold", "reason": "x"}).status_code == 403
    officer = login_verified(_staff("+233200000255", "compliance").phone)
    officer.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "hold", "reason": "Court order"})
    assert KycProfile.objects.get(user=kofi).frozen
    officer.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "release", "reason": "Order lifted"})
    assert not KycProfile.objects.get(user=kofi).frozen
    assert AuditEvent.objects.filter(action="user.hold").exists() and AuditEvent.objects.filter(action="user.release").exists()


def test_disable_sign_in_ends_sessions(kofi):
    ops = login_verified(_staff("+233200000256", "operations").phone)
    before = kofi.token_generation
    ops.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "disable", "reason": "SIM swap reported"})
    kofi.refresh_from_db()
    assert not kofi.is_active and kofi.token_generation == before + 1
    ops.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "enable", "reason": "Verified in branch"})
    kofi.refresh_from_db()
    assert kofi.is_active
    support = login_verified(_staff("+233200000257", "support").phone)
    assert support.post(f"/dashboard/admin/users/{kofi.pk}/", {"action": "disable", "reason": "x"}).status_code == 403


def test_transactions_page_filters_and_export(kofi):
    fin = _staff("+233200000258", "finance")
    c = login_verified(fin.phone)
    page = c.get("/dashboard/admin/transactions/")
    assert b"SP-CC0000001" in page.content and b"SP-CC0000002" in page.content
    failed = c.get("/dashboard/admin/transactions/?status=failed")
    assert b"SP-CC0000002" in failed.content and b"SP-CC0000001" not in failed.content
    assert c.get("/dashboard/admin/transactions/?partial=summary").status_code == 200
    csv = c.get("/dashboard/admin/transactions/export.csv")
    body = csv.content.decode()
    assert csv["Content-Type"] == "text/csv" and "SP-CC0000001" in body and "50.00" in body
    assert "+233244000201" not in body                                         # masked in exports too
    assert AuditEvent.objects.filter(action="transactions.export", actor=fin).exists()
    support = login_verified(_staff("+233200000259", "support").phone)
    assert support.get("/dashboard/admin/transactions/export.csv").status_code == 403


def test_payments_and_risk_pages_render(kofi):
    fin = login_verified(_staff("+233200000260", "finance").phone)
    assert b"Payment gateways" in fin.get("/dashboard/admin/payments/").content
    officer = login_verified(_staff("+233200000261", "compliance").phone)
    Alert.objects.create(rule="X", title="Structuring pattern", severity="high", subject_kind="customer",
                         subject_id=str(kofi.pk), subject_label="Kofi", user=kofi)
    page = officer.get("/dashboard/admin/risk/")
    assert b"Structuring pattern" in page.content and b"open high-severity case" in page.content
