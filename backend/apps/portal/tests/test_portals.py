"""
Portal integration tests (Django test client).

These check routing, access control, and that the portal correctly drives the
underlying services — including the maker-checker rule enforced through the UI.
"""

import pytest
from django.contrib.auth import get_user_model
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.licensing.gate import _enabled_set
from apps.merchants import onboarding, settlement
from apps.merchants.models import ApiKey, Merchant, Settlement, SettlementAccount
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache

from .helpers import PASSWORD, current_token, login, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "PSP_MEDIUM"
    reset_rail_cache()
    MockRail.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def merchant_owner(db):
    user = User.objects.create_user(phone="+233200000010", full_name="Owner", password=PASSWORD)
    m = onboarding.create_merchant(owner=user, legal_name="Shop Ltd", business_type="registered")
    onboarding.submit_for_review(m)
    onboarding.begin_review(m)
    onboarding.approve(m)
    return user, m


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        phone="+233200000020", full_name="Staff", password=PASSWORD, user_type="staff"
    )


_login = login
_current_token = current_token


def _staff_login(user):
    """Log a staff user in and complete 2FA."""
    return login_verified(user.phone)


def test_login_routes_merchant_and_staff_to_their_portals(merchant_owner, staff_user):
    owner, _ = merchant_owner
    r = login_verified(owner.phone).get("/dashboard/")
    assert r.status_code == 200
    assert b"Available for settlement" in r.content

    r = _staff_login(staff_user).get("/dashboard/admin/")
    assert r.status_code == 200
    assert b"Platform overview" in r.content


def test_staff_must_enrol_and_pass_2fa(staff_user):
    c = _login(staff_user.phone)
    # Before 2FA, admin pages redirect to the 2FA flow.
    r = c.get("/dashboard/admin/")
    assert r.status_code == 302 and "/2fa/" in r["Location"]
    # Visiting 2FA creates a pending device and shows the enrolment QR.
    setup = c.get("/dashboard/2fa/")
    assert setup.status_code == 200
    assert b"two-factor" in setup.content.lower()
    device = TOTPDevice.objects.get(user=staff_user, confirmed=False)
    # Confirm with the current code → device becomes confirmed, session verified.
    r = c.post("/dashboard/2fa/", {"token": _current_token(device)})
    assert r.status_code == 200 and b"Save your backup codes" in r.content   # shown once
    device.refresh_from_db()
    assert device.confirmed
    assert c.get("/dashboard/admin/").status_code == 200


def test_integrations_page_lists_connectors(staff_user):
    r = _staff_login(staff_user).get("/dashboard/admin/integrations/")
    assert r.status_code == 200
    body = r.content.decode()
    for name in ("ECG", "Ghana Water", "GhIPSS Instant Pay", "Visa", "Zeepay"):
        assert name in body
    assert "placeholder" in body


def test_safeguarding_page_records_balance_and_runs_check(staff_user):
    from apps.safeguarding.models import SafeguardingCheck, TrustAccount
    account = TrustAccount.objects.create(bank_name="Test Bank", account_name="Trust",
                                          account_last4="1234")
    c = _staff_login(staff_user)
    assert c.get("/dashboard/admin/safeguarding/").status_code == 200
    c.post("/dashboard/admin/safeguarding/",
           {"action": "record", "account": str(account.id), "balance": "1000.00"})
    assert account.balances.count() == 1
    assert SafeguardingCheck.objects.latest("created_at").status == SafeguardingCheck.Status.OK
    # Garbage input is refused, not stored.
    c.post("/dashboard/admin/safeguarding/",
           {"action": "record", "account": str(account.id), "balance": "-5"})
    assert account.balances.count() == 1


def test_merchant_qr_page_and_request_lifecycle(merchant_owner):
    from apps.merchants.models import PaymentRequest
    owner, merchant = merchant_owner
    c = login_verified(owner.phone)
    page = c.get("/dashboard/qr/")
    assert page.status_code == 200
    assert merchant.short_code.encode() in page.content          # printable code shown
    assert b"data:image/svg+xml" in page.content                 # QR rendered

    r = c.post("/dashboard/qr/", {"amount": "25.00", "description": "Order 7"})
    req = PaymentRequest.objects.get(merchant=merchant)
    assert r.status_code == 302 and r["Location"].endswith(f"/dashboard/qr/{req.token}/")
    assert req.amount_minor == 25_00

    status_partial = c.get(f"/dashboard/qr/{req.token}/status/")
    assert b"Waiting for the customer" in status_partial.content

    c.post(f"/dashboard/qr/{req.token}/", {"action": "cancel"})
    req.refresh_from_db()
    assert req.status == PaymentRequest.Status.CANCELLED
    # Garbage amount is refused.
    assert c.post("/dashboard/qr/", {"amount": "abc"}).status_code == 302
    assert PaymentRequest.objects.count() == 1


def test_merchant_cannot_reach_admin(merchant_owner):
    owner, _ = merchant_owner
    r = login_verified(owner.phone).get("/dashboard/admin/")
    assert r.status_code == 403


def test_merchant_can_create_api_key_via_portal(merchant_owner):
    owner, merchant = merchant_owner
    c = login_verified(owner.phone)
    r = c.post("/dashboard/api-keys/", {"mode": "test"})
    assert r.status_code == 200
    assert b"shown only once" in r.content
    assert ApiKey.objects.filter(merchant=merchant).count() == 1


def test_staff_approves_merchant_through_portal(staff_user, db):
    applicant = User.objects.create_user(phone="+233200000030", full_name="New", password=PASSWORD)
    m = onboarding.create_merchant(owner=applicant, legal_name="Fresh Ltd", business_type="sole_trader")
    onboarding.submit_for_review(m)

    c = _staff_login(staff_user)
    c.post(f"/dashboard/admin/merchants/{m.id}/", {"action": "begin_review"})
    c.post(f"/dashboard/admin/merchants/{m.id}/", {"action": "approve", "risk_tier": "low"})

    m.refresh_from_db()
    assert m.status == Merchant.Status.APPROVED


def test_maker_cannot_approve_own_settlement_in_portal(staff_user, merchant_owner):
    """A staff member who requested a settlement cannot approve it from the queue."""
    owner, merchant = merchant_owner
    # Fund the merchant so there is a balance, then create a settlement that needs
    # approval, requested BY the staff user (the maker).
    _fund(merchant)
    acct = SettlementAccount.objects.create(
        merchant=merchant, kind="momo", provider="mtn", account_no="+233244058519",
        account_name="Shop", name_check_status="matched",
    )
    s = settlement.request_settlement(
        merchant=merchant, destination=acct, requested_by=staff_user,
        amount_minor=60_000_00,  # above threshold → needs approval
    )
    assert s.status == Settlement.Status.AWAITING_APPROVAL

    c = _staff_login(staff_user)
    c.post("/dashboard/admin/settlements/", {"settlement": str(s.id), "action": "approve"})
    s.refresh_from_db()
    assert s.status == Settlement.Status.AWAITING_APPROVAL   # still blocked (maker == checker)


def _fund(merchant):
    import json

    from apps.merchants import checkout
    from apps.payments import services
    from apps.rails.types import Network, RailStatus
    p = checkout.initiate_merchant_charge(
        merchant=merchant, amount_minor=70_000_00, network=Network.MTN,
        payer="+233244058519", idempotency_key="fund-portal",
    )
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
