"""The add_staff command: creates staff with roles, changes roles, refuses customer numbers."""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.common.models import AuditEvent

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_create_then_change_roles():
    with mock.patch("getpass.getpass", return_value="Str0ng-Passw0rd!x"):
        call_command("add_staff", phone="0244000111", name="Ama Mensah", roles="finance, operations")
    u = User.objects.get(phone="+233244000111")
    assert u.user_type == "staff" and u.check_password("Str0ng-Passw0rd!x")
    assert set(u.groups.values_list("name", flat=True)) == {"finance", "operations"}

    call_command("add_staff", phone="0244000111", roles="compliance")
    assert set(u.groups.values_list("name", flat=True)) == {"compliance"}
    assert AuditEvent.objects.filter(action__in=("staff.created", "staff.roles_changed")).count() == 2


def test_refuses_bad_roles_and_customer_numbers():
    with pytest.raises(CommandError, match="Roles must be"):
        call_command("add_staff", phone="0244000111", name="X", roles="superuser")
    User.objects.create_user(phone="+233244000222")
    with pytest.raises(CommandError, match="customer"):
        call_command("add_staff", phone="0244000222", roles="finance")
