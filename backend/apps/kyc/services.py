"""KYC tier upgrades and compliance holds."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.encryption import lookup_hash
from apps.connectors.identity import GHANA_CARD_RE
from apps.connectors.placeholder import ConnectorNotImplemented
from apps.connectors.registry import identity_connector
from apps.common.audit import audited

from .exceptions import VerificationFailed
from .limits import profile_for
from .models import KycProfile


def upgrade_to_verified(user, ghana_card_number: str) -> KycProfile:
    """Tier 0 → 1: verify the customer's Ghana Card with NIA / the KYC provider."""
    card = ghana_card_number.strip().upper()
    if not GHANA_CARD_RE.match(card):
        raise VerificationFailed("Enter the Ghana Card number as GHA-123456789-0.")

    profile = profile_for(user)
    if profile.tier >= KycProfile.Tier.VERIFIED:
        return profile

    card_hash = lookup_hash(card)
    if KycProfile.objects.filter(ghana_card_hash=card_hash).exclude(pk=profile.pk).exists():
        # One card, one SokoPay identity. Don't say which account it's linked to.
        raise VerificationFailed("This Ghana Card is already linked to another account.")

    connector = identity_connector()
    if not connector.available:
        raise VerificationFailed("Identity verification is not available yet. Please try later.")
    try:
        result = connector.verify_ghana_card(card, user.phone)
    except ConnectorNotImplemented as exc:
        raise VerificationFailed("Identity verification is not available yet.") from exc
    if not result.verified:
        raise VerificationFailed(result.message or "We couldn't verify this Ghana Card.")

    with transaction.atomic():
        profile.tier = KycProfile.Tier.VERIFIED
        profile.ghana_card_number = card
        profile.ghana_card_hash = card_hash
        profile.verified_name = result.full_name
        profile.verified_at = timezone.now()
        profile.save()
        if result.full_name and not user.full_name:
            user.full_name = result.full_name.title()
            user.save(update_fields=["full_name", "updated_at"])
    # AML: screen the now-verified legal name against sanctions / PEP lists.
    from apps.compliance.screening import screen_user
    screen_user(user)
    return profile


MAX_SELFIE_BYTES = 5 * 1024 * 1024
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")   # JPEG, PNG


def upgrade_to_enhanced(user, selfie: bytes) -> KycProfile:
    """
    Tier 1 → 2: a live selfie matched against the Ghana Card photo.

    The image is sent to the identity provider and then discarded; SokoPay keeps only
    the outcome, score and the provider's reference.
    """
    profile = profile_for(user)
    if profile.tier >= KycProfile.Tier.ENHANCED:
        return profile
    if profile.tier < KycProfile.Tier.VERIFIED:
        raise VerificationFailed("Verify your Ghana Card first.")
    if not selfie or len(selfie) > MAX_SELFIE_BYTES:
        raise VerificationFailed("Please send a JPEG or PNG selfie under 5 MB.")
    if not selfie.startswith(_IMAGE_MAGIC):
        raise VerificationFailed("Please send a JPEG or PNG selfie.")

    connector = identity_connector()
    if not connector.available:
        raise VerificationFailed("Selfie verification is not available yet. Please try later.")
    try:
        result = connector.verify_liveness(selfie, profile.ghana_card_number)
    except ConnectorNotImplemented as exc:
        raise VerificationFailed("Selfie verification is not available yet.") from exc
    if not result.passed:
        raise VerificationFailed(result.message or "We couldn't verify your selfie. Try again "
                                 "in good light with your face fully visible.")

    profile.tier = KycProfile.Tier.ENHANCED
    profile.liveness_verified_at = timezone.now()
    profile.liveness_score = round(result.match_score, 3)
    profile.liveness_provider_ref = result.provider_ref[:64]
    profile.save(update_fields=["tier", "liveness_verified_at", "liveness_score",
                                "liveness_provider_ref", "updated_at"])
    return profile


@audited("kyc.hold", fields=("reason",))
def freeze(user, *, reason: str, actor) -> KycProfile:
    """Compliance hold: stop money leaving the wallet (e.g. suspected fraud, court order)."""
    profile = profile_for(user)
    profile.frozen = True
    profile.frozen_reason = reason[:255]
    profile.frozen_at = timezone.now()
    profile.frozen_by = actor
    profile.save(update_fields=["frozen", "frozen_reason", "frozen_at", "frozen_by", "updated_at"])
    return profile


@audited("kyc.release")
def unfreeze(user, *, actor) -> KycProfile:
    profile = profile_for(user)
    profile.frozen = False
    profile.frozen_reason = f"Released by {actor}"
    profile.save(update_fields=["frozen", "frozen_reason", "updated_at"])
    return profile
