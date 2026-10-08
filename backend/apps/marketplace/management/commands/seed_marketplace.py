"""
Seed the financial marketplace.

* Two template providers (insurance, lending) wired to placeholder connectors and left
  INACTIVE — clone and activate one per real partner once the agreement is signed.
* With --dev, two ACTIVE mock providers and their sample products, for local
  development and demos only. Never run --dev against production.

Run:  python manage.py seed_marketplace [--dev]
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.connectors.registry import FINANCIAL
from apps.marketplace.models import FinancialProduct, FinancialProvider

TEMPLATES = [
    ("insurance-partner", "Insurance partner (template)", "insurance",
     "National Insurance Commission (NIC)", "insurance_partner"),
    ("lending-partner", "Lending partner (template)", "lending",
     "Bank of Ghana", "lending_partner"),
    ("savings-partner", "Savings partner (template)", "savings",
     "Bank of Ghana", "savings_partner"),
    ("investment-partner", "Investment partner (template)", "investment",
     "Securities and Exchange Commission (SEC)", "investment_partner"),
    ("pension-partner", "Pension partner (template)", "pension",
     "National Pensions Regulatory Authority (NPRA)", "pension_partner"),
]

MOCKS = [
    ("mock-insurer", "Mock Insurer (dev)", "insurance", "—", "mock_insurance"),
    ("mock-lender", "Mock Lender (dev)", "lending", "—", "mock_lending"),
]


class Command(BaseCommand):
    help = "Seed marketplace providers (templates; add --dev for mock data)."

    def add_arguments(self, parser):
        parser.add_argument("--dev", action="store_true", help="Also create mock providers/products.")

    def handle(self, *args, dev=False, **options):
        if dev and not getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False):
            raise CommandError("--dev creates mock providers; refused because "
                               "ALLOW_MOCK_INTEGRATIONS is off (production settings).")
        for key, name, category, regulator, connector in TEMPLATES:
            FinancialProvider.objects.update_or_create(
                key=key, defaults={"name": name, "category": category, "regulator": regulator,
                                   "connector": connector, "is_active": False},
            )
        if dev:
            for key, name, category, regulator, connector in MOCKS:
                provider, _ = FinancialProvider.objects.update_or_create(
                    key=key, defaults={"name": name, "category": category,
                                       "regulator": regulator, "connector": connector,
                                       "is_active": True},
                )
                for info in FINANCIAL[connector]().list_products():
                    FinancialProduct.objects.update_or_create(
                        code=info.code,
                        defaults={"provider": provider, "name": info.name,
                                  "category": info.category, "summary": info.summary,
                                  "min_minor": info.min_minor, "max_minor": info.max_minor},
                    )
        self.stdout.write(self.style.SUCCESS("Marketplace seeded."))
