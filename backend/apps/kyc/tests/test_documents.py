"""
Customer profile (address, Ghana Post GPS address, email, phone read-only) and identity
documents: Ghana Card checked with NIA at once, passport / driver's licence reviewed by
compliance, photos encrypted at rest and visible only to compliance staff.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.common.models import AuditEvent
from apps.kyc.models import IdentityDocument, KycProfile
from apps.licensing.gate import _enabled_set
from apps.portal.tests.helpers import PASSWORD, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 3000
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 3000
FUTURE = (date.today() + timedelta(days=400)).isoformat()


@pytest.fixture(autouse=True)
def _env(settings, tmp_path):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.KYC_IDENTITY_PROVIDER = "mock"
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.FIELD_ENCRYPTION_KEY = "test-field-key"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def kofi(db):
    u = User.objects.create_user(phone="+233244000201", full_name="Kofi Asante")
    u.set_pin("482915")
    u.save()
    return u


def _client(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION="Bearer " + tokens.issue_tokens(user)["access"])
    return c


def _upload(c, **data):
    files = {"front": SimpleUploadedFile("front.jpg", JPEG, content_type="image/jpeg")}
    files.update(data.pop("files", {}))
    return c.post("/api/v1/kyc/documents", {**data, **files}, format="multipart")


# --- profile ------------------------------------------------------------------------------
def test_profile_returns_phone_read_only_and_new_fields(kofi):
    r = _client(kofi).get("/api/v1/auth/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["phone"] == "+233244000201" and body["phone_locked"] is True
    assert body["address"] == "" and body["gps_address"] == ""
    assert body["verification"] == {"ghana_card_verified": False, "ghana_card_hint": "", "documents": []}


def test_profile_saves_address_gps_and_email(kofi):
    c = _client(kofi)
    r = c.patch("/api/v1/auth/profile", {"address": "  12 Liberation Rd,  Osu, Accra ", "gps_address": "ga 183 8164",
                                         "email": "Kofi@Example.com"}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["address"] == "12 Liberation Rd, Osu, Accra"
    assert body["gps_address"] == "GA-183-8164" and body["email"] == "kofi@example.com"
    assert c.patch("/api/v1/auth/profile", {"gps_address": "AK-0123-4567"}, format="json").json()["gps_address"] == "AK-0123-4567"


@pytest.mark.parametrize("gps", ["12345", "GA-1-2", "GAX-183-8164", "Accra"])
def test_bad_gps_addresses_are_refused(kofi, gps):
    r = _client(kofi).patch("/api/v1/auth/profile", {"gps_address": gps}, format="json")
    assert r.status_code == 400 and "GA-183-8164" in r.json()["error"]


def test_phone_cannot_be_changed_through_profile(kofi):
    _client(kofi).patch("/api/v1/auth/profile", {"phone": "+233244999999"}, format="json")
    kofi.refresh_from_db()
    assert kofi.phone == "+233244000201"


# --- documents ----------------------------------------------------------------------------
def test_ghana_card_is_checked_with_nia_and_verified_immediately(kofi, settings):
    c = _client(kofi)
    r = _upload(c, doc_type="ghana_card", number="gha-123456789-0",
                files={"back": SimpleUploadedFile("back.png", PNG, content_type="image/png")})
    assert r.status_code == 201, r.content
    doc = IdentityDocument.objects.get(user=kofi)
    assert doc.status == "verified" and doc.number == "GHA-123456789-0" and doc.back_key
    assert KycProfile.objects.get(user=kofi).tier == 1                       # same as the tier-1 upgrade
    assert r.json()["ghana_card_verified"] is True
    on_disk = (Path(settings.MEDIA_ROOT) / doc.front_key).read_bytes()
    assert JPEG[:200] not in on_disk                                          # encrypted at rest
    assert c.get("/api/v1/auth/profile").json()["name_locked"] is True


def test_ghana_card_failing_nia_is_not_saved(kofi):
    r = _upload(_client(kofi), doc_type="ghana_card", number="GHA-000000000-0")
    assert r.status_code == 400 and "No record" in r.json()["error"]
    assert not IdentityDocument.objects.exists()


def test_a_different_ghana_card_from_the_verified_one_is_refused(kofi):
    c = _client(kofi)
    _upload(c, doc_type="ghana_card", number="GHA-123456789-0")
    r = _upload(c, doc_type="ghana_card", number="GHA-987654321-0")
    assert r.status_code == 400 and "already verified" in r.json()["error"]


def test_passport_waits_for_review_and_a_new_upload_replaces_the_pending_one(kofi):
    c = _client(kofi)
    r = _upload(c, doc_type="passport", number="g1234567", expiry_date=FUTURE)
    assert r.status_code == 201
    first = IdentityDocument.objects.get(user=kofi)
    assert first.status == "pending" and first.number_last4 == "4567"
    item = r.json()["documents"][0]
    assert item["type_label"] == "Passport" and item["status"] == "pending" and item["number_hint"] == "••••4567"
    _upload(c, doc_type="passport", number="G7654321", expiry_date=FUTURE)
    first.refresh_from_db()
    assert first.status == "replaced"
    assert len(c.get("/api/v1/kyc/documents").json()["documents"]) == 1


@pytest.mark.parametrize("data,message", [
    ({"doc_type": "passport", "number": "G1234567"}, "expiry date"),
    ({"doc_type": "drivers_licence", "number": "DL-1234567", "expiry_date": "2020-01-01"}, "expired"),
    ({"doc_type": "passport", "number": "!!", "expiry_date": FUTURE}, "exactly as printed"),
    ({"doc_type": "voter_id", "number": "123", "expiry_date": FUTURE}, "Document type"),
])
def test_bad_document_details_are_refused(kofi, data, message):
    r = _upload(_client(kofi), **data)
    assert r.status_code == 400 and message in r.json()["error"]


def test_photos_must_be_images(kofi):
    r = _upload(_client(kofi), doc_type="passport", number="G1234567", expiry_date=FUTURE,
                files={"front": SimpleUploadedFile("x.pdf", b"%PDF-1.4" + b"x" * 500)})
    assert r.status_code == 400 and "JPEG or PNG" in r.json()["error"]
    r = _client(kofi).post("/api/v1/kyc/documents", {"doc_type": "passport", "number": "G1234567",
                                                     "expiry_date": FUTURE}, format="multipart")
    assert r.status_code == 400 and "photo of the front" in r.json()["error"]


# --- back office review ----------------------------------------------------------------------
@pytest.fixture
def compliance(db):
    u = User.objects.create_user(phone="+233200000204", full_name="Kojo Compliance", password=PASSWORD,
                                 user_type="staff")
    u.groups.add(Group.objects.get(name="compliance"))
    return u


def test_compliance_reviews_a_licence(kofi, compliance):
    _upload(_client(kofi), doc_type="drivers_licence", number="DL-1234567", expiry_date=FUTURE)
    doc = IdentityDocument.objects.get()
    c = login_verified(compliance.phone)
    queue = c.get("/dashboard/admin/kyc/")
    assert b"Driver&#x27;s licence" in queue.content and b"4567" in queue.content
    page = c.get(f"/dashboard/admin/kyc/documents/{doc.pk}/")
    assert page.status_code == 200 and b"DL-1234567" in page.content
    img = c.get(f"/dashboard/admin/kyc/documents/{doc.pk}/front/")
    assert img.status_code == 200 and img.content == JPEG and img["Cache-Control"] == "no-store"
    r = c.post(f"/dashboard/admin/kyc/documents/{doc.pk}/", {"action": "reject", "note": ""}, follow=True)
    assert b"Say why" in r.content
    c.post(f"/dashboard/admin/kyc/documents/{doc.pk}/", {"action": "approve"})
    doc.refresh_from_db()
    assert doc.status == "verified" and doc.reviewed_by == compliance
    assert AuditEvent.objects.filter(action="kyc.document_review").exists()
    assert AuditEvent.objects.filter(action="kyc.document_view").exists()
    assert kofi.notifications.filter(title__icontains="verified").exists()


def test_only_compliance_can_see_id_photos(kofi):
    _upload(_client(kofi), doc_type="passport", number="G1234567", expiry_date=FUTURE)
    doc = IdentityDocument.objects.get()
    support = User.objects.create_user(phone="+233200000208", full_name="Support", password=PASSWORD,
                                       user_type="staff")
    support.groups.add(Group.objects.get(name="support"))
    c = login_verified(support.phone)
    assert c.get(f"/dashboard/admin/kyc/documents/{doc.pk}/front/").status_code == 403
