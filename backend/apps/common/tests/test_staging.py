"""Staging: production hardening, mock partner allowed but superuser+2FA only, random demo logins."""

import importlib
import os
import subprocess
import sys

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory

from apps.common import devtools

pytestmark = pytest.mark.django_db


def _env(**extra):
    from cryptography.fernet import Fernet
    return {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.staging", "SECRET_KEY": "s" * 60,
            "ALLOWED_HOSTS": "test.example.com", "DATABASE_URL": "sqlite:///:memory:",
            "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(), "DEBUG": "False", **extra}


def test_staging_settings_are_hardened_but_allow_the_mock_partner():
    code = ("from django.conf import settings as s; import django; django.setup(); "
            "print(s.DEBUG, s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, s.ALLOW_MOCK_INTEGRATIONS, "
            "s.RAIL_PROVIDER, bool(s.ENVIRONMENT_BANNER))")
    out = subprocess.run([sys.executable, "-c", code], env=_env(), capture_output=True, text=True, cwd=os.getcwd())
    assert out.stdout.split() == ["False", "True", "True", "True", "mock", "True"], out.stderr[-500:]
    # Without the encryption key it refuses to start, like production.
    bad = _env()
    bad.pop("FIELD_ENCRYPTION_KEY")
    bad["FIELD_ENCRYPTION_KEY"] = ""
    assert subprocess.run([sys.executable, "-c", code], env=bad, capture_output=True).returncode != 0


def test_mock_partner_on_a_server_needs_a_verified_superuser(settings):
    settings.DEBUG, settings.STAGING_TOOLS, settings.ALLOW_MOCK_INTEGRATIONS = False, True, True
    assert devtools.enabled()
    req = RequestFactory().get("/dev/mock-partner/")
    from django.contrib.auth.models import AnonymousUser
    req.user = AnonymousUser()
    assert devtools.mock_partner(req).status_code == 302           # sent to sign in
    admin = get_user_model().objects.create_user(phone="+233200000001", user_type="staff", is_superuser=True)
    admin.is_verified = lambda: False
    req.user = admin
    assert devtools.mock_partner(req).status_code == 302           # 2FA not done
    settings.ALLOW_MOCK_INTEGRATIONS = False
    assert not devtools.enabled()                                  # production: never


def test_seed_on_a_server_uses_random_credentials(settings, tmp_path):
    settings.DEBUG, settings.ALLOW_MOCK_INTEGRATIONS = False, True
    out = tmp_path / "creds.md"
    import apps.common.management.commands.seed_demo as seed
    importlib.reload(seed)
    call_command("seed_demo", out=str(out))
    text = out.read_text(encoding="utf-8")
    assert "SokoDemo#2026" not in text and "428173" not in text
    assert "N354T55FZT5THZDZNM5YN5AO5ZBHHJ6O" not in text            # the guide's fixed 2FA secret
    importlib.reload(seed)
