"""Embedded financial products (DEMI): premiums from the wallet, loans into it."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.exceptions import CapabilityNotLicensed
from apps.licensing.gate import _enabled_set
from apps.marketplace import services
from apps.marketplace.models import FinancialProduct, ProductApplication
from apps.wallet import services as wallet

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    call_command("seed_marketplace", dev=True)
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def ama(db):
    user = User.objects.create_user(phone="+233244058519", full_name="Ama")
    # Give Ama GH₵200 of e-money (as a top-up would).
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 200_00),
                        credit(accounts.customer_wallet(str(user.id)), 200_00)])
    return user


def _approved(user, code, amount=None):
    app = services.apply(user=user, product=FinancialProduct.objects.get(code=code),
                         amount_minor=amount, consent=True)
    return services.set_status(app, ProductApplication.Status.APPROVED, "Approved (test)")


def test_premium_paid_from_wallet_to_insurer(ama):
    app = _approved(ama, "INS-FUNERAL")
    services.pay_premium(user=ama, application=app, amount_minor=30_00)
    assert wallet.balance(ama) == 170_00
    owed = natural_balance_of(accounts.financial_partner_payable("mock-insurer"))
    assert owed == 30_00
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_premium_needs_an_approved_insurance_application(ama):
    app = services.apply(user=ama, product=FinancialProduct.objects.get(code="INS-FUNERAL"),
                         amount_minor=None, consent=True)          # still "submitted"
    with pytest.raises(services.MarketplaceError):
        services.pay_premium(user=ama, application=app, amount_minor=10_00)


def test_loan_disbursed_to_wallet_once(ama):
    app = _approved(ama, "LOAN-SME-STOCK", amount=100_000)
    services.disburse_loan(application=app, amount_minor=300_00, partner_ref="LND-1")
    services.disburse_loan(application=app, amount_minor=300_00, partner_ref="LND-1")  # retry
    assert wallet.balance(ama) == 500_00
    assert app.transactions.count() == 1
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_embedded_products_need_demi(settings, ama):
    app = _approved(ama, "INS-FUNERAL")
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_ENHANCED"   # marketplace yes, wallet money no
    _enabled_set.cache_clear()
    with pytest.raises(CapabilityNotLicensed):
        services.pay_premium(user=ama, application=app, amount_minor=10_00)


def test_pay_premium_api(ama):
    app = _approved(ama, "INS-HEALTH-BASIC")
    client = APIClient()
    client.force_authenticate(user=ama)
    apps_list = client.get("/api/v1/marketplace/applications").json()
    assert apps_list[0]["category"] == "insurance"
    r = client.post(f"/api/v1/marketplace/applications/{app.reference}/pay-premium",
                    {"amount": "25.00"}, format="json")
    assert r.status_code == 201, r.content
    # Someone else can't pay against Ama's application.
    other = User.objects.create_user(phone="+233200000077")
    client.force_authenticate(user=other)
    r = client.post(f"/api/v1/marketplace/applications/{app.reference}/pay-premium",
                    {"amount": "25.00"}, format="json")
    assert r.status_code == 404
