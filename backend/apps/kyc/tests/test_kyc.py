"""
KYC tiers and wallet limits: Ghana Card encrypted at rest, one card per identity,
tier upgrade through the identity connector, and every limit enforced from the ledger.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APIClient

from apps.kyc import limits, services
from apps.kyc.exceptions import LimitExceeded, VerificationFailed, WalletFrozen
from apps.kyc.models import KycProfile
from apps.licensing.gate import _enabled_set
from apps.payments import services as payment_services
from apps.rails.mock import MockRail
from apps.rails.registry import reset_rail_cache
from apps.rails.types import Network, RailStatus
from apps.wallet import services as wallet

User = get_user_model()
pytestmark = pytest.mark.django_db
CARD = "GHA-123456789-0"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.RAIL_PROVIDER = "mock"
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.KYC_IDENTITY_PROVIDER = "mock"
    reset_rail_cache()
    MockRail.reset()
    _enabled_set.cache_clear()
    yield
    reset_rail_cache()


@pytest.fixture
def ama(db):
    return User.objects.create_user(phone="+233244058519")


def _fund(user, amount_minor, key):
    p = wallet.initiate_funding(user=user, amount_minor=amount_minor, network=Network.MTN,
                                payer=user.phone, idempotency_key=key)
    MockRail.drive(p.rail_ref, RailStatus.SUCCEEDED)
    body = json.dumps({"provider_ref": p.rail_ref, "status": "succeeded"}).encode()
    payment_services.handle_webhook("mock", {"X-Mock-Signature": MockRail().sign(body)}, body)


# --- identity & encryption ------------------------------------------------------
def test_upgrade_verifies_card_and_encrypts_it_at_rest(ama):
    profile = services.upgrade_to_verified(ama, CARD)
    assert profile.tier == KycProfile.Tier.VERIFIED
    assert profile.verified_name.startswith("TEST PERSON")
    ama.refresh_from_db()
    assert ama.full_name                               # filled from the ID record

    profile.refresh_from_db()
    assert profile.ghana_card_number == CARD           # decrypts transparently
    with connection.cursor() as cur:                   # but the stored value is ciphertext
        cur.execute("SELECT ghana_card_number FROM kyc_profile")
        stored = [r[0] for r in cur.fetchall()]
    assert len(stored) == 1 and stored[0]
    assert CARD not in stored[0] and "123456789" not in stored[0]


def test_bad_format_and_unknown_card_rejected(ama):
    with pytest.raises(VerificationFailed):
        services.upgrade_to_verified(ama, "12345")
    with pytest.raises(VerificationFailed):
        services.upgrade_to_verified(ama, "GHA-000000000-0")   # mock: no NIA record
    assert limits.profile_for(ama).tier == 0


def test_one_card_one_identity(ama):
    services.upgrade_to_verified(ama, CARD)
    other = User.objects.create_user(phone="+233209998888")
    with pytest.raises(VerificationFailed):
        services.upgrade_to_verified(other, CARD.lower())     # same card, different case


def test_placeholder_identity_provider_refuses_cleanly(settings, ama):
    settings.KYC_IDENTITY_PROVIDER = "nia"
    with pytest.raises(VerificationFailed):
        services.upgrade_to_verified(ama, CARD)


# --- selfie liveness (tier 1 → 2) -----------------------------------------------
JPEG = b"\xff\xd8\xff" + b"\x00" * 2000          # a JPEG header padded to > 1 KB


def test_selfie_upgrade_to_enhanced(ama):
    with pytest.raises(VerificationFailed):         # Ghana Card must come first
        services.upgrade_to_enhanced(ama, JPEG)
    services.upgrade_to_verified(ama, CARD)
    with pytest.raises(VerificationFailed):         # not an image
        services.upgrade_to_enhanced(ama, b"not-an-image" * 200)
    with pytest.raises(VerificationFailed):         # provider: face doesn't match
        services.upgrade_to_enhanced(ama, b"\xff\xd8\xff" + b"FAIL" + b"\x00" * 2000)
    assert limits.profile_for(ama).tier == 1

    profile = services.upgrade_to_enhanced(ama, JPEG)
    assert profile.tier == KycProfile.Tier.ENHANCED
    assert float(profile.liveness_score) > 0.9
    assert limits.tier_limits(2)["monthly_out"] is None   # BoG: unlimited


def test_selfie_api(ama):
    from django.core.files.uploadedfile import SimpleUploadedFile
    client = APIClient()
    client.force_authenticate(user=ama)
    client.post("/api/v1/kyc/upgrade", {"ghana_card_number": CARD}, format="json")
    r = client.post("/api/v1/kyc/upgrade/selfie",
                    {"selfie": SimpleUploadedFile("me.jpg", JPEG, content_type="image/jpeg")},
                    format="multipart")
    assert r.status_code == 200, r.content
    assert r.json()["tier"] == 2
    assert r.json()["limits"]["monthly_out"] == "Unlimited"


# --- limits (Bank of Ghana tiers) -------------------------------------------------
def test_balance_cap_and_per_transaction_limit_on_credit(ama):
    with pytest.raises(LimitExceeded):   # > GH₵3,000 per transaction
        wallet.initiate_funding(user=ama, amount_minor=3_500_00, network=Network.MTN)
    _fund(ama, 3_000_00, "f1")
    _fund(ama, 1_500_00, "f2")
    with pytest.raises(LimitExceeded):   # 4,500 + 1,000 > GH₵5,000 balance cap
        wallet.initiate_funding(user=ama, amount_minor=1_000_00, network=Network.MTN)


def test_daily_outflow_limit(ama):
    for phone in ("+233200000001", "+233200000002", "+233200000003"):
        User.objects.create_user(phone=phone)
    _fund(ama, 3_000_00, "f1")
    _fund(ama, 2_000_00, "f2")                                   # at the 5,000 cap
    wallet.send_p2p(sender=ama, recipient_phone="+233200000001", amount_minor=1_500_00)
    wallet.send_p2p(sender=ama, recipient_phone="+233200000002", amount_minor=1_000_00)
    _fund(ama, 2_500_00, "f3")           # plenty of balance, so only the limit can stop it
    with pytest.raises(LimitExceeded):   # 2,500 already sent today + 500.01 > 3,000 daily
        wallet.send_p2p(sender=ama, recipient_phone="+233200000003", amount_minor=500_01)
    wallet.send_p2p(sender=ama, recipient_phone="+233200000003", amount_minor=500_00)  # exactly at
    assert limits.usage(ama)["daily_out"] == 3_000_00


def test_upgrade_raises_limits(ama):
    services.upgrade_to_verified(ama, CARD)
    p = wallet.initiate_funding(user=ama, amount_minor=10_000_00, network=Network.MTN)
    assert p.amount_minor == 10_000_00


def test_frozen_wallet_blocks_money_out_but_not_in(ama):
    User.objects.create_user(phone="+233200000001")
    _fund(ama, 300_00, "f1")
    staff = User.objects.create_user(phone="+233200000099", user_type="staff")
    services.freeze(ama, reason="Suspected fraud", actor=staff)
    with pytest.raises(WalletFrozen):
        wallet.send_p2p(sender=ama, recipient_phone="+233200000001", amount_minor=10_00)
    _fund(ama, 100_00, "f2")                    # credits still allowed
    assert wallet.balance(ama) == 400_00
    services.unfreeze(ama, actor=staff)
    wallet.send_p2p(sender=ama, recipient_phone="+233200000001", amount_minor=10_00)


# --- API -----------------------------------------------------------------------
def test_kyc_api(ama):
    client = APIClient()
    client.force_authenticate(user=ama)
    state = client.get("/api/v1/kyc").json()
    assert state["tier"] == 0 and state["limits"]["max_balance"] == "GH₵ 5,000.00"
    assert state["next_step"] == "ghana_card"

    bad = client.post("/api/v1/kyc/upgrade", {"ghana_card_number": "nope"}, format="json")
    assert bad.status_code == 400

    ok = client.post("/api/v1/kyc/upgrade", {"ghana_card_number": CARD}, format="json")
    assert ok.status_code == 200 and ok.json()["tier"] == 1
    assert ok.json()["ghana_card"].endswith("789-0")


def test_limit_errors_reach_the_app_as_400(ama):
    client = APIClient()
    client.force_authenticate(user=ama)
    r = client.post("/api/v1/wallet/fund", {"amount": "3500.00", "network": "mtn"}, format="json")
    assert r.status_code == 400
    assert "per-transaction limit" in r.json()["error"]
