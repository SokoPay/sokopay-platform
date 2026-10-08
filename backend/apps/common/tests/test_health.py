"""Readiness probe, ops snapshot (the numbers alarms watch), JSON log lines, health page."""

import json
import logging

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client

from apps.common.health import log_ops_snapshot, ops_snapshot
from apps.common.logging import JsonFormatter
from apps.portal.tests.helpers import PASSWORD, login_verified

pytestmark = pytest.mark.django_db


def test_readyz_and_healthz():
    c = Client()
    assert c.get("/healthz").status_code == 200
    r = c.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_snapshot_has_every_alarmed_number_and_logs_json(caplog):
    snap = ops_snapshot()
    for key in ("stuck_settlements", "stuck_refunds", "aml_high_open", "safeguarding", "payments_failed_15m"):
        assert key in snap and isinstance(snap[key], int)
    assert snap["safeguarding"] == 1                     # no check recorded yet = stale/missing
    with caplog.at_level(logging.INFO, logger="sokopay.metrics"):
        log_ops_snapshot()
    rec = next(r for r in caplog.records if r.name == "sokopay.metrics")
    line = json.loads(JsonFormatter().format(rec))
    assert line["metric"] == "ops" and line["level"] == "INFO" and "stuck_refunds" in line


def test_health_page_for_staff_only():
    User = get_user_model()
    ops = User.objects.create_user(phone="+233200000080", password=PASSWORD, user_type="staff")
    ops.groups.add(Group.objects.get(name="support"))
    page = login_verified(ops.phone).get("/dashboard/admin/health/")
    assert page.status_code == 200 and b"Safeguarding" in page.content
    customer = User.objects.create_user(phone="+233244000001", password=PASSWORD)
    c = Client()
    c.login(phone=customer.phone, password=PASSWORD)
    assert c.get("/dashboard/admin/health/").status_code in (302, 403)
