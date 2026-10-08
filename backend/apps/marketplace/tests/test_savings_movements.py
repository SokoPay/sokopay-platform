"""Savings / investment / pension: pay in from the wallet, withdraw back when the provider pays."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Sum
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set
from apps.marketplace import services
from apps.marketplace.models import FinancialProduct, FinancialProvider, ProductApplication
from apps.portal.tests.helpers import PASSWORD, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def ama(db):
    u = User.objects.create_user(phone="+233244058519", full_name="Ama Serwaa")
    u.set_pin(PIN)
    u.save()
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 500_00),
                        credit(accounts.customer_wallet(str(u.id)), 500_00)])
    return u


def _account(user, category="savings", min_minor=10_00):
    prov = FinancialProvider.objects.create(key=f"{category}-bank", name=f"{category.title()} Bank", category=category,
                                            regulator="BoG", connector="mock", is_active=True)
    prod = FinancialProduct.objects.create(provider=prov, code=f"{category}-1", name=f"Easy {category}",
                                           category=category, min_minor=min_minor)
    return ProductApplication.objects.create(reference=f"MP-{category[:6]}", user=user, product=prod,
                                             status="approved", consent_given_at=timezone.now())


def _wallet(u):
    return natural_balance_of(accounts.customer_wallet(str(u.id)))


def test_contribute_then_withdraw_when_provider_pays(ama):
    app = _account(ama)
    services.contribute(user=ama, application=app, amount_minor=100_00, pin=PIN)
    assert _wallet(ama) == 400_00
    assert natural_balance_of(accounts.financial_partner_payable("savings-bank")) == 100_00
    with pytest.raises(services.MarketplaceError, match="minimum"):
        services.contribute(user=ama, application=app, amount_minor=5_00, pin=PIN)
    with pytest.raises(services.MarketplaceError):
        services.contribute(user=ama, application=app, amount_minor=10_00, pin="000000")

    services.request_withdrawal(user=ama, application=app, amount_minor=60_00, pin=PIN)
    assert _wallet(ama) == 400_00                                  # nothing moves on request
    with pytest.raises(services.MarketplaceError, match="already"):
        services.request_withdrawal(user=ama, application=app, amount_minor=10_00, pin=PIN)
    with pytest.raises(services.MarketplaceError, match="match"):
        services.confirm_withdrawal(application=app, amount_minor=70_00, partner_ref="SB-1")
    services.confirm_withdrawal(application=app, amount_minor=60_00, partner_ref="SB-1")
    services.confirm_withdrawal(application=app, amount_minor=60_00, partner_ref="SB-1")   # retry: no double credit
    assert _wallet(ama) == 460_00
    pos = services.position(app)
    assert pos["pending_withdrawal_display"] is None
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


def test_pension_withdrawal_goes_through_trustee_and_others_cant_use_my_account(ama):
    app = _account(ama, "pension")
    with pytest.raises(services.MarketplaceError, match="NPRA"):
        services.request_withdrawal(user=ama, application=app, amount_minor=10_00, pin=PIN)
    kofi = User.objects.create_user(phone="+233244000123")
    kofi.set_pin(PIN)
    kofi.save()
    with pytest.raises(services.MarketplaceError, match="not found"):
        services.contribute(user=kofi, application=app, amount_minor=10_00, pin=PIN)


def test_api_and_ops_confirmation(ama):
    app = _account(ama)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(ama)['access']}")
    r = c.post(f"/api/v1/marketplace/applications/{app.reference}/contribute", {"amount": "50.00", "pin": PIN},
               format="json")
    assert r.status_code == 201, r.content
    c.post(f"/api/v1/marketplace/applications/{app.reference}/withdraw", {"amount": "20.00", "pin": PIN}, format="json")
    listing = c.get("/api/v1/marketplace/applications").json()
    assert listing[0]["position"]["pending_withdrawal_display"]

    ops = User.objects.create_user(phone="+233200000070", password=PASSWORD, user_type="staff")
    ops.groups.add(Group.objects.get(name="operations"))
    o = login_verified(ops.phone)
    txn = app.transactions.get(kind="withdrawal")
    assert o.get("/dashboard/admin/withdrawals/").status_code == 200
    o.post("/dashboard/admin/withdrawals/", {"txn": txn.pk, "amount": "20.00", "partner_ref": "SB-9"})
    assert _wallet(ama) == 500_00 - 50_00 + 20_00
