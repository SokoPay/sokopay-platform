"""Audit trail: append-only, recorded for privileged actions, linked to the request id."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client

from apps.common.audit import record
from apps.common.models import AuditEvent
from apps.kyc import services as kyc
from apps.portal.tests.helpers import PASSWORD, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_events_are_append_only():
    e = record("test.action", summary="hello")
    e.summary = "changed"
    with pytest.raises(ValueError):
        e.save()
    with pytest.raises(ValueError):
        e.delete()


@pytest.mark.skipif(connection.vendor != "postgresql", reason="database trigger is PostgreSQL-only")
def test_database_refuses_changes_to_audit_rows():
    from django.db import DatabaseError, transaction
    record("test.action", summary="hello")
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.all().update(summary="tampered")


def test_service_actions_are_recorded_with_the_actor():
    officer = User.objects.create_user(phone="+233200000101", full_name="Officer A", user_type="staff")
    cust = User.objects.create_user(phone="+233244058519")
    kyc.freeze(cust, reason="Police letter", actor=officer)
    kyc.unfreeze(cust, actor=officer)
    hold, release = AuditEvent.objects.filter(action__in=("kyc.hold", "kyc.release")).order_by("at")
    assert hold.actor == officer and "Police letter" in hold.summary and hold.data["reason"] == "Police letter"
    assert release.action == "kyc.release" and "Officer A" in release.actor_label


def test_role_grants_are_recorded():
    u = User.objects.create_user(phone="+233200000102", user_type="staff")
    u.groups.add(Group.objects.get(name="finance"))
    u.is_superuser = True
    u.save()
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert "role.add" in actions and "user.privilege_change" in actions


def test_request_id_flows_to_response_and_audit():
    officer = User.objects.create_user(phone="+233200000103", password=PASSWORD, user_type="staff")
    officer.groups.add(Group.objects.get(name="compliance"))
    cust = User.objects.create_user(phone="+233244058520")
    c = login_verified(officer.phone)
    r = c.post("/dashboard/admin/kyc/", {"account": cust.phone, "reason": "Court order", "action": "hold"},
               HTTP_X_REQUEST_ID="trace-abc-123456")
    assert r["X-Request-ID"] == "trace-abc-123456"
    e = AuditEvent.objects.get(action="kyc.hold")
    assert e.request_id == "trace-abc-123456" and e.actor == officer
    assert Client().get("/healthz")["X-Request-ID"]                       # generated when absent
    assert Client().get("/healthz", HTTP_X_REQUEST_ID="bad id <script>")["X-Request-ID"] != "bad id <script>"


def test_audit_page_access_and_export_is_itself_audited():
    fin = User.objects.create_user(phone="+233200000104", password=PASSWORD, user_type="staff")
    fin.groups.add(Group.objects.get(name="finance"))
    sup = User.objects.create_user(phone="+233200000105", password=PASSWORD, user_type="staff")
    sup.groups.add(Group.objects.get(name="support"))
    record("test.action", summary="visible")
    f = login_verified(fin.phone)
    assert b"test.action" in f.get("/dashboard/admin/audit/").content
    assert b"test.action" in f.get("/dashboard/admin/audit/", {"format": "csv"}).content
    assert AuditEvent.objects.filter(action="audit.export", actor=fin).exists()
    assert login_verified(sup.phone).get("/dashboard/admin/audit/").status_code == 403


def test_ledger_entries_carry_the_request_id():
    from apps.common import audit
    from apps.ledger import accounts
    from apps.ledger.services import credit, debit, post_entry
    token = audit._request_id.set("req-ledger-42")
    try:
        e = post_entry("traced", [debit(accounts.partner_clearing("mock"), 1_00),
                                  credit(accounts.customer_wallet("11111111-1111-1111-1111-111111111111"), 1_00)])
    finally:
        audit._request_id.reset(token)
    assert e.request_id == "req-ledger-42"
