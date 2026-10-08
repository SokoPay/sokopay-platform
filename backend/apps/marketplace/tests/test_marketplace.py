"""Financial marketplace: licence gate, consent, partner placeholders, API."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.marketplace import services
from apps.marketplace.models import FinancialProduct, FinancialProvider, ProductApplication

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _enhanced(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_ENHANCED"
    _enabled_set.cache_clear()
    call_command("seed_marketplace", dev=True)
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def user(db):
    return User.objects.create_user(phone="+233244058519", full_name="Ama")


@pytest.mark.parametrize("licence", ["PSP_STANDARD", "PSP_MEDIUM"])
def test_blocked_below_enhanced(settings, licence):
    settings.SOKOPAY_ACTIVE_LICENCE = licence
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        list(services.list_products())


def test_only_active_providers_are_listed():
    codes = {p.code for p in services.list_products()}
    assert {"INS-HEALTH-BASIC", "INS-FUNERAL", "LOAN-SME-STOCK"} <= codes
    # Template providers are seeded inactive and have no products shown.
    assert not FinancialProvider.objects.get(key="insurance-partner").is_active
    assert {p.category for p in services.list_products("lending")} == {"lending"}


def test_application_requires_consent(user):
    product = FinancialProduct.objects.get(code="INS-FUNERAL")
    with pytest.raises(services.MarketplaceError):
        services.apply(user=user, product=product, amount_minor=5_000, consent=False)
    assert ProductApplication.objects.count() == 0


def test_application_submitted_to_partner(user):
    product = FinancialProduct.objects.get(code="LOAN-SME-STOCK")
    app = services.apply(user=user, product=product, amount_minor=100_000, consent=True)
    assert app.status == ProductApplication.Status.SUBMITTED
    assert app.partner_ref.startswith("APP-")
    assert app.consent_given_at is not None


def test_amount_limits(user):
    product = FinancialProduct.objects.get(code="LOAN-SME-STOCK")   # 500.00 – 20,000.00
    with pytest.raises(services.MarketplaceError):
        services.apply(user=user, product=product, amount_minor=100, consent=True)


def test_placeholder_partner_refused_and_nothing_recorded(user):
    template = FinancialProvider.objects.get(key="lending-partner")
    template.is_active = True
    template.save()
    product = FinancialProduct.objects.create(provider=template, code="REAL-LOAN",
                                              name="Real loan", category="lending")
    with pytest.raises(services.MarketplaceError):
        services.apply(user=user, product=product, amount_minor=None, consent=True)
    assert ProductApplication.objects.count() == 0


def test_api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    r = client.get("/api/v1/marketplace/products", {"category": "insurance"})
    assert r.status_code == 200 and len(r.json()) == 2
    r = client.post("/api/v1/marketplace/apply",
                    {"product_code": "INS-HEALTH-BASIC", "amount": "50.00", "consent": True},
                    format="json")
    assert r.status_code == 201, r.content
    assert len(client.get("/api/v1/marketplace/applications").json()) == 1
