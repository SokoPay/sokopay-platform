"""
Targeted attacks on the customer app's money, sign-in and data boundaries.

Each test states what a secure system must do; a failure is a finding.
  A  Authentication: brute force, enumeration, token theft, disabled accounts
  B  Step-up: every debit from a wallet needs the PIN, checked on the server
  C  Money integrity: bad amounts, overdraft, double submission, holds, limits
  D  Other people's data (IDOR): records belonging to another customer
  E  Partner callbacks and the USSD gateway: forged / replayed requests
  F  Information leakage
"""

import datetime as dt
import json

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import otp, tokens
from apps.kyc import services as kyc
from apps.notifications.sms.registry import reset_sms_cache
from apps.payments.models import Payment
from apps.wallet import services as wallet
from apps.wallet.models import SavedRecipient

from .conftest import PIN, client_for, fund, make_customer

User = get_user_model()
pytestmark = pytest.mark.django_db


def bal(user) -> int:
    return wallet.balance(user)


# --- A. authentication ------------------------------------------------------------------------
def test_otp_codes_cannot_be_brute_forced(anon):
    phone = "+233244000299"
    code = otp.request_otp(phone)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(otp.MAX_VERIFY_ATTEMPTS):
        anon.post("/api/v1/auth/otp/verify", {"phone": phone, "code": wrong}, format="json")
    r = anon.post("/api/v1/auth/otp/verify", {"phone": phone, "code": code}, format="json")
    assert r.status_code == 400 and "tokens" not in r.json()


def test_otp_requests_are_rate_limited(anon, settings):
    settings.SMS_PROVIDER = "console"
    reset_sms_cache()
    phone = "+233244000298"
    codes = [anon.post("/api/v1/auth/otp/request", {"phone": phone}, format="json").status_code
             for _ in range(otp.MAX_REQUESTS_PER_WINDOW + 1)]
    assert codes[-1] == 429


def test_pin_brute_force_locks_the_account(kofi, anon):
    for _ in range(5):
        anon.post("/api/v1/auth/login", {"phone": kofi.phone, "pin": "000001"}, format="json")
    r = anon.post("/api/v1/auth/login", {"phone": kofi.phone, "pin": PIN}, format="json")
    assert r.status_code in (400, 401, 403, 423, 429) and "tokens" not in r.json()


def test_login_does_not_reveal_which_numbers_have_accounts(kofi, anon):
    known = anon.post("/api/v1/auth/login", {"phone": kofi.phone, "pin": "000001"}, format="json")
    unknown = anon.post("/api/v1/auth/login", {"phone": "+233244999999", "pin": "000001"}, format="json")
    assert known.status_code == unknown.status_code and known.json() == unknown.json()


def test_pin_reset_needs_the_ghana_card_once_verified(kofi, anon):
    kyc.upgrade_to_verified(kofi, "GHA-123456789-0")
    code = otp.request_otp(kofi.phone)
    r = anon.post("/api/v1/auth/pin/reset", {"phone": kofi.phone, "code": code, "new_pin": "604213"}, format="json")
    assert r.status_code == 400
    kofi.refresh_from_db()
    assert kofi.check_pin(PIN)


def test_a_reused_refresh_token_ends_every_session(kofi):
    """Refresh-token theft: if a spent refresh token comes back, assume theft and sign out everywhere."""
    pair = tokens.issue_tokens(kofi)
    first = APIClient().post("/api/v1/auth/refresh", {"refresh": pair["refresh"]}, format="json")
    assert first.status_code == 200
    new_access = first.json()["tokens"]["access"]
    replay = APIClient().post("/api/v1/auth/refresh", {"refresh": pair["refresh"]}, format="json")
    assert replay.status_code in (400, 401)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {new_access}")
    assert c.get("/api/v1/wallet").status_code == 401        # the legitimate-looking new session is gone too


def test_disabled_and_closed_accounts_lose_access_at_once(kofi):
    c = client_for(kofi)
    User.objects.filter(pk=kofi.pk).update(is_active=False)
    assert c.get("/api/v1/wallet").status_code in (401, 403)


# --- B. step-up: the PIN on every debit -----------------------------------------------------------
def _debits(ama):
    return {
        "p2p": ("/api/v1/wallet/send", {"recipient": ama.phone, "amount": "10.00"}),
        "transfer": ("/api/v1/wallet/transfer", {"destination_type": "momo", "institution": "mtn",
                                                  "account": "+233244111222", "amount": "10.00"}),
    }


@pytest.mark.parametrize("kind", ["p2p", "transfer"])
def test_wallet_debits_need_the_pin_on_the_server(kind, kofi, ama):
    path, body = _debits(ama)[kind]
    c = client_for(kofi)
    before = bal(kofi)
    r = c.post(path, body, format="json")                                    # no PIN at all
    assert r.status_code in (400, 403) and "PIN" in json.dumps(r.json()).upper(), (kind, r.status_code, r.content[:200])
    r = c.post(path, {**body, "pin": "000001"}, format="json")              # wrong PIN
    assert r.status_code in (400, 403)
    assert bal(kofi) == before
    r = c.post(path, {**body, "pin": PIN}, format="json")                   # right PIN
    assert r.status_code in (200, 201), r.content[:300]


def test_pay_merchant_and_wallet_bills_need_the_pin(kofi):
    from apps.merchants import onboarding
    from apps.payments.models import Biller
    owner = make_customer("+233200000301", "Grace")
    m = onboarding.create_merchant(owner=owner, legal_name="Grace Ltd", business_type="registered")
    for step in (onboarding.submit_for_review, onboarding.begin_review, onboarding.approve):
        step(m)
    m.refresh_from_db()
    Biller.objects.create(code="ECG", name="ECG", category="electricity", rail_biller_code="ECG", is_active=True)
    c = client_for(kofi)
    before = bal(kofi)
    r1 = c.post("/api/v1/wallet/pay-merchant", {"code": m.short_code, "amount": "5.00"}, format="json")
    r2 = c.post("/api/v1/payments/bill", {"biller_code": "ECG", "account": "0123456789", "amount": "5.00",
                                          "source": "wallet"}, format="json")
    assert r1.status_code in (400, 403) and "PIN" in json.dumps(r1.json()).upper(), r1.content[:200]
    assert r2.status_code in (400, 403) and "PIN" in json.dumps(r2.json()).upper(), r2.content[:200]
    assert bal(kofi) == before


def test_wrong_step_up_pins_count_towards_the_lockout(kofi, ama):
    c = client_for(kofi)
    for _ in range(5):
        c.post("/api/v1/wallet/send", {"recipient": ama.phone, "amount": "1.00", "pin": "000001"}, format="json")
    r = c.post("/api/v1/wallet/send", {"recipient": ama.phone, "amount": "1.00", "pin": PIN}, format="json")
    assert r.status_code in (400, 403, 423, 429)


# --- C. money integrity ------------------------------------------------------------------------------
@pytest.mark.parametrize("amount", ["0", "-5", "0.001", "10.005", "abc", "1e3", "NaN", "Infinity", "99999999999"])
def test_bad_amounts_never_move_money(amount, kofi, ama):
    before_k, before_a = bal(kofi), bal(ama)
    r = client_for(kofi).post("/api/v1/wallet/send", {"recipient": ama.phone, "amount": amount, "pin": PIN},
                              format="json")
    assert r.status_code == 400, (amount, r.status_code)
    assert (bal(kofi), bal(ama)) == (before_k, before_a)


def test_no_overdraft_and_no_self_send(kofi, ama):
    c = client_for(kofi)
    over = c.post("/api/v1/wallet/send", {"recipient": ama.phone, "amount": "500.01", "pin": PIN}, format="json")
    me = c.post("/api/v1/wallet/send", {"recipient": kofi.phone, "amount": "1.00", "pin": PIN}, format="json")
    assert over.status_code == 400 and me.status_code == 400 and bal(kofi) == 500_00


def test_the_same_request_sent_twice_moves_money_once(kofi, ama):
    c = client_for(kofi)
    body = {"recipient": ama.phone, "amount": "10.00", "pin": PIN}
    c.post("/api/v1/wallet/send", body, format="json", HTTP_IDEMPOTENCY_KEY="tap-1")
    c.post("/api/v1/wallet/send", body, format="json", HTTP_IDEMPOTENCY_KEY="tap-1")
    assert bal(kofi) == 490_00 and bal(ama) == 310_00


def test_a_held_wallet_cannot_send(kofi, ama):
    kyc.freeze(kofi, reason="test", actor=None)
    r = client_for(kofi).post("/api/v1/wallet/send", {"recipient": ama.phone, "amount": "1.00", "pin": PIN},
                              format="json")
    assert r.status_code == 400 and bal(kofi) == 500_00


def test_tier_limits_apply(kofi, ama):
    # Tier 0 per-transaction limit is below GH₵ 500 in the default schedule.
    from apps.kyc.limits import tier_limits
    cap = tier_limits(0)["max_txn"]
    if cap is None or cap >= 500_00:
        pytest.skip("tier 0 has no per-transaction cap below the test balance")
    fund(kofi, cap, key="top")
    r = client_for(kofi).post("/api/v1/wallet/send", {"recipient": ama.phone, "amount": f"{(cap + 100) / 100:.2f}",
                                                      "pin": PIN}, format="json")
    assert r.status_code == 400 and "limit" in r.json()["error"].lower()


def test_funding_only_credits_after_a_verified_partner_callback(kofi, anon):
    c = client_for(kofi)
    before = bal(kofi)
    r = c.post("/api/v1/wallet/fund", {"amount": "50.00", "network": "mtn"}, format="json")
    assert r.status_code == 201 and bal(kofi) == before                     # nothing until the partner confirms
    ref = Payment.objects.filter(user=kofi, purpose="wallet_fund").latest("created_at").rail_ref
    forged = anon.post("/api/v1/rails/mock/webhook", data=json.dumps({"provider_ref": ref, "status": "succeeded"}),
                       content_type="application/json", HTTP_X_MOCK_SIGNATURE="forged")
    assert forged.status_code in (400, 401, 403) and bal(kofi) == before


def test_a_replayed_partner_callback_credits_once(kofi):
    p = fund(kofi, 20_00, key="once")
    from apps.payments import services as payment_services
    from apps.rails.mock import MockRail
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    after_first = bal(kofi)
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)
    assert bal(kofi) == after_first


# --- D. other people's data --------------------------------------------------------------------------
def test_cannot_read_another_customers_payment(kofi, ama):
    theirs = Payment.objects.filter(user=ama).first()
    r = client_for(kofi).get(f"/api/v1/payments/{theirs.reference}")
    assert r.status_code == 404


def test_cannot_touch_another_customers_saved_recipients(kofi, ama):
    s = SavedRecipient.objects.create(owner=ama, kind="momo", label="Mum", value="0244111222")
    c = client_for(kofi)
    assert c.delete(f"/api/v1/wallet/saved/{s.pk}").status_code == 404
    assert SavedRecipient.objects.filter(pk=s.pk).exists()
    assert "Mum" not in json.dumps(c.get("/api/v1/wallet/saved").json())


def test_cannot_read_or_mark_another_customers_notifications(kofi, ama):
    from apps.notifications.services import notify
    note = notify(ama, title="Secret", body="Your balance is GH₵ 1,000,000")
    c = client_for(kofi)
    assert c.post(f"/api/v1/notifications/{note.pk}/read").status_code == 404
    assert "Secret" not in json.dumps(c.get("/api/v1/notifications").json())


def test_cannot_see_another_customers_activity_or_documents(kofi, ama):
    c = client_for(kofi)
    feed = json.dumps(c.get("/api/v1/activity").json())
    assert "Ama" not in feed or "Serwaa" not in feed
    assert c.get("/api/v1/kyc/documents").json()["documents"] == []


def test_cannot_decide_another_customers_cash_out(kofi, ama):
    from apps.agents import services as agents
    from apps.agents.models import CashOutRequest
    agent_user = make_customer("+233240000101", "Esi")
    agent = agents.register_agent(user=agent_user, display_name="Esi's Kiosk")
    agents.activate_agent(agent)
    req = CashOutRequest.objects.create(agent=agent, customer=ama, amount_minor=10_00,
                                        expires_at=timezone.now() + dt.timedelta(minutes=5))
    r = client_for(kofi).post(f"/api/v1/wallet/cash-out-requests/{req.pk}/approve", {"pin": PIN}, format="json")
    assert r.status_code in (400, 403, 404) and "not found" in r.json()["error"].lower()
    req.refresh_from_db()
    assert req.status == "pending" and bal(ama) == 300_00


def test_cannot_remove_another_customers_push_device(kofi, ama):
    from apps.notifications.models import Device
    Device.objects.create(user=ama, platform="android", token="ama-device-token")
    client_for(kofi).delete("/api/v1/devices/ama-device-token")
    assert Device.objects.filter(token="ama-device-token", user=ama).exists()


# --- E. partner callbacks and USSD ----------------------------------------------------------------------
def test_unsigned_remittance_callbacks_are_refused(kofi, anon):
    before = bal(kofi)
    r = anon.post("/api/v1/remittance/mock/webhook",
                  data=json.dumps({"partner_ref": "R1", "recipient_phone": kofi.phone, "amount_minor": 1_000_00,
                                   "sender_name": "X", "sender_country": "GB"}),
                  content_type="application/json")
    assert r.status_code in (400, 401, 403, 404) and bal(kofi) == before


def test_ussd_needs_the_gateway_secret(anon, settings):
    settings.USSD_SHARED_SECRET = "s3cret"
    r = anon.post("/api/v1/ussd/callback?key=wrong", {"sessionId": "1", "phoneNumber": "+233244000201", "text": ""})
    assert r.status_code in (401, 403)


# --- F. information leakage -----------------------------------------------------------------------------------
def test_recipient_lookup_shows_a_short_name_only(kofi, ama):
    r = client_for(kofi).get(f"/api/v1/wallet/send/lookup?account={ama.phone}")
    name = r.json()["name"]
    assert "Serwaa" not in name and ama.phone not in json.dumps(r.json())


def test_recipient_lookups_are_throttled_against_number_harvesting(kofi):
    c = client_for(kofi)
    codes = [c.get(f"/api/v1/wallet/send/lookup?account=02440{i:05d}").status_code for i in range(61)]
    assert 429 in codes


def test_api_schema_and_docs_are_not_public_in_production(anon, settings):
    import importlib

    from django.urls import clear_url_caches

    import config.urls
    settings.DEBUG = False
    try:
        importlib.reload(config.urls)
        clear_url_caches()
        assert anon.get("/api/docs/").status_code == 404
        assert anon.get("/api/schema/").status_code == 404
    finally:
        settings.DEBUG = True
        importlib.reload(config.urls)
        clear_url_caches()
