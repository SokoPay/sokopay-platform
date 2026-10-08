"""Compliance back-office: role-gated access, alert case work, STR, screening, list upload."""

import io

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.compliance import alerts
from apps.compliance.models import Alert, ScreeningMatch, SuspiciousTransactionReport, WatchlistEntry
from apps.kyc.limits import profile_for

from .helpers import PASSWORD, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db


def _staff(phone, name, *groups):
    u = User.objects.create_user(phone=phone, full_name=name, password=PASSWORD, user_type="staff")
    for g in groups:
        u.groups.add(Group.objects.get(name=g))
    return u


@pytest.fixture
def officer(db):
    return _staff("+233200000101", "Officer A", "compliance")


@pytest.fixture
def officer2(db):
    return _staff("+233200000102", "Officer B", "compliance")


@pytest.fixture
def case(db):
    u = User.objects.create_user(phone="+233244000201", full_name="Kofi Suspect")
    return alerts.raise_alert(rule="PASS_THROUGH", title="Money in and straight out", severity="high",
                              subject_kind="customer", subject_id=str(u.id), subject_label="Kofi Suspect",
                              user=u, evidence={"inflow": "GH₵ 3,000.00"})


def test_only_compliance_role_can_see_aml(case):
    support = _staff("+233200000103", "Support", "support")
    c = login_verified(support.phone)
    for url in ("/dashboard/admin/compliance/", f"/dashboard/admin/compliance/alerts/{case.pk}/",
                "/dashboard/admin/compliance/watchlists/"):
        assert c.get(url).status_code == 403, url
    customer = User.objects.create_user(phone="+233244000202", password=PASSWORD)
    from django.test import Client
    cc = Client()
    cc.login(phone=customer.phone, password=PASSWORD)
    assert cc.get("/dashboard/admin/compliance/").status_code in (302, 403)


def test_case_work_hold_note_str_and_four_eyes(officer, officer2, case):
    c = login_verified(officer.phone)
    page = c.get("/dashboard/admin/compliance/")
    assert page.status_code == 200 and b"PASS_THROUGH" in page.content
    detail = c.get(f"/dashboard/admin/compliance/alerts/{case.pk}/")
    assert b"Kofi Suspect" in detail.content and b"GH" in detail.content

    url = f"/dashboard/admin/compliance/alerts/{case.pk}/"
    c.post(url, {"action": "assign"})
    c.post(url, {"action": "hold", "text": "Looks like a mule"})
    assert profile_for(case.user).frozen
    c.post(url, {"action": "note", "text": "Called partner bank."})
    r = c.post(url, {"action": "str", "text": "Received GH₵3,000 from unrelated senders and forwarded it within minutes to a single account."})
    report = SuspiciousTransactionReport.objects.get()
    assert r["Location"].endswith(f"/compliance/reports/{report.pk}/")

    # Preparer can't approve; second officer can; then file with FIC reference.
    c.post(f"/dashboard/admin/compliance/reports/{report.pk}/", {"action": "approve"})
    report.refresh_from_db()
    assert report.status == "draft"
    c2 = login_verified(officer2.phone)
    c2.post(f"/dashboard/admin/compliance/reports/{report.pk}/", {"action": "approve"})
    c2.post(f"/dashboard/admin/compliance/reports/{report.pk}/", {"action": "file", "fic_reference": "FIC-77"})
    report.refresh_from_db()
    case.refresh_from_db()
    assert report.status == "filed" and case.status == "closed_reported"
    dl = c2.get(f"/dashboard/admin/compliance/reports/{report.pk}/download/")
    assert dl.status_code == 200 and b"FIC-77" in dl.content
    kinds = list(case.notes.values_list("kind", flat=True))
    assert {"assign", "hold", "note", "status"} <= set(kinds)


def test_close_requires_reason(officer, case):
    c = login_verified(officer.phone)
    c.post(f"/dashboard/admin/compliance/alerts/{case.pk}/", {"action": "close", "text": ""})
    case.refresh_from_db()
    assert case.is_open


def test_watchlist_upload_and_screening_review(officer, django_capture_on_commit_callbacks):
    u = User.objects.create_user(phone="+233244000203", full_name="Kwame Asante Mensah")
    c = login_verified(officer.phone)
    f = io.BytesIO(b"name,kind\nKwame Asante Mensah,sanctions\n")
    f.name = "gh.csv"
    with django_capture_on_commit_callbacks(execute=True):
        c.post("/dashboard/admin/compliance/watchlists/", {"source": "gh", "format": "csv", "file": f})
    assert WatchlistEntry.objects.filter(source="gh").count() == 1
    m = ScreeningMatch.objects.get(user=u)
    assert b"Kwame Asante Mensah" in c.get("/dashboard/admin/compliance/screening/").content
    c.post("/dashboard/admin/compliance/screening/", {"match": m.pk, "decision": "clear", "reason": "DOB differs"})
    m.refresh_from_db()
    assert m.status == "cleared" and not profile_for(u).frozen


def test_admin_dashboard_flags_open_high_alerts(officer, case):
    c = login_verified(officer.phone)
    assert b"high-severity fraud/AML alert" in c.get("/dashboard/admin/").content
    assert Alert.objects.filter(severity="high").count() == 1
