"""Onboarding state machine, API keys, and role setup."""

import pytest

from apps.merchants import api_keys, onboarding
from apps.merchants.exceptions import InvalidTransition
from apps.merchants.models import ApiKey, Merchant, MerchantMember

pytestmark = pytest.mark.django_db


def test_create_merchant_starts_draft_with_owner_member(owner):
    m = onboarding.create_merchant(
        owner=owner, legal_name="Kofi Trading", business_type="sole_trader"
    )
    assert m.status == Merchant.Status.DRAFT
    member = MerchantMember.objects.get(merchant=m, user=owner)
    assert member.role == MerchantMember.Role.OWNER


def test_happy_onboarding_path(owner):
    m = onboarding.create_merchant(owner=owner, legal_name="X Ltd", business_type="registered")
    onboarding.submit_for_review(m)
    assert m.status == Merchant.Status.SUBMITTED
    onboarding.begin_review(m)
    assert m.status == Merchant.Status.IN_REVIEW
    onboarding.approve(m, risk_tier=Merchant.RiskTier.LOW)
    assert m.status == Merchant.Status.APPROVED
    assert m.is_live


def test_cannot_approve_directly_from_draft(owner):
    m = onboarding.create_merchant(owner=owner, legal_name="X", business_type="registered")
    with pytest.raises(InvalidTransition):
        onboarding.approve(m)


def test_rejected_merchant_can_resubmit(owner):
    m = onboarding.create_merchant(owner=owner, legal_name="X", business_type="registered")
    onboarding.submit_for_review(m)
    onboarding.reject(m, reason="Blurry ID photo")
    assert m.status == Merchant.Status.REJECTED
    assert m.rejection_reason == "Blurry ID photo"
    onboarding.submit_for_review(m)
    assert m.status == Merchant.Status.SUBMITTED
    assert m.rejection_reason == ""


def test_api_key_issue_and_verify(approved_merchant):
    key, raw = api_keys.issue_key(approved_merchant, ApiKey.Mode.LIVE)
    assert raw.startswith("sk_live_")
    # The raw secret is never stored, only its hash.
    assert key.key_hash != raw
    assert api_keys.verify_key(raw).id == key.id
    # A tampered key does not verify.
    assert api_keys.verify_key(raw + "x") is None


def test_revoked_key_does_not_verify(approved_merchant):
    key, raw = api_keys.issue_key(approved_merchant, ApiKey.Mode.TEST)
    api_keys.revoke_key(key)
    assert api_keys.verify_key(raw) is None
