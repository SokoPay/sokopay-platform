"""DEMO ONLY: print the current 6-digit 2FA code for a demo portal account.

    python manage.py demo_2fa_code +233200000004
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Print the current 2FA code for a demo portal account (dev/demo only)."

    def add_arguments(self, parser):
        parser.add_argument("phone")

    def handle(self, *args, phone, **opts):
        if not (getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False)
                and (settings.DEBUG or getattr(settings, "STAGING_TOOLS", False))):
            raise CommandError("Only available on a development / demo setup.")
        from django_otp.oath import totp
        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.filter(user__phone=phone, confirmed=True).first()
        if device is None:
            raise CommandError("No 2FA device for that phone. Is the demo data loaded?")
        code = str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(device.digits)
        self.stdout.write(f"{code}   (valid for up to 30 seconds)")
