"""Statements (customer wallet + merchant balance), single-use download links, regulatory figures."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.compliance import regulatory
from apps.ledger import accounts, statements
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding, qr
from apps.merchants.models import MerchantMember
from apps.portal.tests.helpers import PASSWORD, login_verified
from apps.wallet import merchant_pay

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.QR_BASE_URL = "https://pay.test"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def world(db):
    owner = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    shop = onboarding.create_merchant(owner=owner, legal_name="Ama Stores Ltd", business_type="registered",
                                      trading_name="=Ama Stores")      # formula-looking name on purpose
    onboarding.submit_for_review(shop)
    onboarding.begin_review(shop)
    onboarding.approve(shop)
    kofi = User.objects.create_user(phone="+233244058519", full_name="Kofi Mensah")
    post_entry("Wallet top-up", [debit(accounts.partner_clearing("mock"), 500_00),
                                 credit(accounts.customer_wallet(str(kofi.id)), 500_00)])
    p = merchant_pay.pay(user=kofi, code=qr.static_payload(shop), amount_minor=120_00)
    return owner, shop, kofi, p


def _client(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(user)['access']}")
    return c


def test_customer_statement_balances_and_formats(world):
    _, _, kofi, p = world
    today = timezone.localdate()
    st = statements.customer_statement(kofi, today.replace(day=1), today)
    assert st["opening_minor"] == 0 and st["closing_minor"] == 380_00
    assert st["total_in_minor"] == 500_00 and st["total_out_minor"] == 120_00
    assert [r["balance_minor"] for r in st["rows"]] == [500_00, 380_00]
    assert st["rows"][1]["reference"] == p.reference
    assert all(str(kofi.id) not in r["details"] for r in st["rows"])      # no internal ids
    pdf = statements.to_pdf(st)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and b"Kofi Mensah" in pdf
    assert b"380.00" in statements.to_csv(st)


def test_merchant_csv_is_formula_safe_and_opening_balance_carries(world):
    owner, shop, kofi, _ = world
    tomorrow = timezone.localdate()
    st = statements.merchant_statement(shop, tomorrow, tomorrow)
    assert st["closing_minor"] > 0
    csv_text = statements.to_csv(st).decode("utf-8-sig")
    assert ",'=Ama Stores" in csv_text or "'=Ama Stores" in csv_text or "=Ama" not in csv_text.split("\n")[0][:30]
    for bad in (("2026-13-01", None), ("2026-02-01", "2026-01-01"), ("2024-01-01", "2026-01-01")):
        with pytest.raises(statements.StatementError):
            statements.parse_period(*bad)


def test_statement_api_and_single_use_link(world):
    owner, shop, kofi, _ = world
    c = _client(kofi)
    r = c.get("/api/v1/statements")
    assert r.status_code == 200 and r.json()["closing_display"]
    assert c.get("/api/v1/statements", {"file": "pdf"})["Content-Type"] == "application/pdf"
    link = c.post("/api/v1/statements/link", {"file": "pdf"}, format="json").json()["url"]
    anon = APIClient()
    first = anon.get(link)
    assert first.status_code == 200 and first.content.startswith(b"%PDF")
    assert anon.get(link).status_code == 410                      # single use
    assert anon.get(link.replace("t=", "t=x")).status_code == 410  # tampered

    # Merchant scope: money roles only, re-checked when the link is used.
    assert c.get("/api/v1/merchant-app/statements").status_code == 403
    oc = _client(owner)
    assert oc.get("/api/v1/merchant-app/statements", {"file": "csv"}).status_code == 200
    cashier = User.objects.create_user(phone="+233200000011")
    m = MerchantMember.objects.create(merchant=shop, user=cashier, role="finance")
    mlink = _client(cashier).post("/api/v1/statements/link", {"scope": "merchant", "file": "csv"},
                                  format="json").json()["url"]
    m.role = "cashier"
    m.save()
    assert anon.get(mlink).status_code == 403


def test_portal_statement_download_and_regulatory_return(world):
    owner, shop, kofi, _ = world
    c = login_verified(owner.phone)
    assert c.get("/dashboard/statements/").status_code == 200
    r = c.get("/dashboard/statements/", {"format": "pdf"})
    assert r["Content-Type"] == "application/pdf"

    now = timezone.localdate()
    report = regulatory.monthly_return(now.year, now.month)
    assert report["transactions"]["payments_merchant"] == {"count": 1, "value_minor": 120_00}
    assert report["e_money"]["customer_wallets_minor"] == 380_00
    assert report["customers"]["active_in_month"] == 1

    fin = User.objects.create_user(phone="+233200000040", password=PASSWORD, user_type="staff")
    fin.groups.add(Group.objects.get(name="finance"))
    f = login_verified(fin.phone)
    page = f.get("/dashboard/admin/regulatory/", {"month": now.strftime("%Y-%m")})
    assert page.status_code == 200 and b"payments_merchant" in page.content
    csv_resp = f.get("/dashboard/admin/regulatory/", {"month": now.strftime("%Y-%m"), "format": "csv"})
    assert b"customer_wallets_minor" in csv_resp.content
    support = User.objects.create_user(phone="+233200000041", password=PASSWORD, user_type="staff")
    support.groups.add(Group.objects.get(name="support"))
    assert login_verified(support.phone).get("/dashboard/admin/regulatory/").status_code == 403
