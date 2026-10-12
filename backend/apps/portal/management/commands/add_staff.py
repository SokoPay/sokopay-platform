"""
Create a back-office staff login, or change an existing staff member's roles.

  python manage.py add_staff --phone 0244000111 --name "Ama Mensah" --roles finance,operations
  python manage.py add_staff --phone 0244000111 --roles compliance          # change roles only

Roles: compliance, operations, support, finance (any mix). The password is asked for twice
(never on the command line, so it stays out of shell history). The person sets up 2FA the
first time they sign in to /dashboard/. Every change is written to the audit log.
"""

from __future__ import annotations

import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.audit import record as audit
from apps.portal.decorators import STAFF_ROLES
from apps.wallet.accounts_lookup import normalise_phone


class Command(BaseCommand):
    help = "Create a staff login with roles, or change a staff member's roles."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--name", default="", help="Full name (required for a new person).")
        parser.add_argument("--roles", required=True, help=f"Comma-separated: {', '.join(STAFF_ROLES)}")

    def handle(self, *args, phone, name, roles, **opts):
        User = get_user_model()
        number = normalise_phone(phone)
        if not number:
            raise CommandError("Enter a Ghana phone number, like 0244000111.")
        wanted = sorted({r.strip().lower() for r in roles.split(",") if r.strip()})
        unknown = [r for r in wanted if r not in STAFF_ROLES]
        if not wanted or unknown:
            raise CommandError(f"Roles must be from: {', '.join(STAFF_ROLES)}.")

        user = User.objects.filter(phone=number).first()
        if user is not None and user.user_type != "staff":
            raise CommandError("That number belongs to a customer, agent or business account. Use another number for staff.")
        password = None
        if user is None:
            if not name.strip():
                raise CommandError("--name is required for a new staff member.")
            password = self._ask_password()

        with transaction.atomic():
            created = user is None
            if created:
                user = User.objects.create_user(phone=number, full_name=name.strip(), password=password,
                                                user_type="staff")
            elif name.strip():
                user.full_name = name.strip()
                user.save(update_fields=["full_name"])
            user.groups.remove(*Group.objects.filter(name__in=STAFF_ROLES))
            user.groups.add(*[Group.objects.get_or_create(name=r)[0] for r in wanted])
            audit("staff.created" if created else "staff.roles_changed", actor=None, obj=user,
                  roles=",".join(wanted), via="add_staff command")

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} {user.full_name} ({number}) with roles: {', '.join(wanted)}."))
        if created:
            self.stdout.write("They sign in at /dashboard/ and set up 2FA on first sign-in.")

    def _ask_password(self) -> str:
        for _ in range(3):
            first = getpass.getpass("Password for the new staff member: ")
            if first != getpass.getpass("Again: "):
                self.stderr.write("The two passwords don't match.")
                continue
            try:
                validate_password(first)
            except ValidationError as exc:
                self.stderr.write(" ".join(exc.messages))
                continue
            return first
        raise CommandError("No password set.")
