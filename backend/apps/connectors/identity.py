"""
Identity connectors: verify a customer's Ghana Card for KYC tier upgrades.

The Ghana Card is issued by the National Identification Authority (NIA). Verification
is done either directly against NIA or through a licensed KYC provider that has NIA
access. Both are placeholders until an agreement is in place.
"""

from __future__ import annotations

import re

from .base import IdentityConnector
from .placeholder import PlaceholderIdentity
from .types import IdentityResult, LivenessResult

GHANA_CARD_RE = re.compile(r"^GHA-\d{9}-\d$")


class NiaConnector(PlaceholderIdentity):
    key = "nia"
    display_name = "National Identification Authority (Ghana Card) — direct"
    regulator = "National Identification Authority"
    go_live = ("NIA verification-service agreement; API credentials; data-protection "
               "terms for identity data. [VERIFY]")


class KycProviderConnector(PlaceholderIdentity):
    key = "kyc_provider"
    display_name = "KYC provider with NIA access (e.g. Smile ID) — template"
    regulator = "Provider's NIA access agreement; Data Protection Commission"
    go_live = ("Contract with a KYC provider that has NIA access; ID-verification and "
               "liveness APIs; data-processing agreement. [VERIFY]")


class MockIdentityConnector(IdentityConnector):
    """
    Dev/test double.
      * Ghana Card GHA-000000000-0 is 'not found'; anything else well-formed passes.
      * Liveness passes for any image of at least 1 KB, unless b"FAIL" appears in its
        first 64 bytes (after the JPEG/PNG header).
    """

    key = "mock"
    display_name = "Mock identity check"
    regulator = "—"
    go_live = "Never used in production."
    available = True
    is_mock = True

    def verify_ghana_card(self, card_number, phone):
        if not GHANA_CARD_RE.match(card_number or ""):
            return IdentityResult(verified=False, message="Invalid Ghana Card number format.")
        if card_number == "GHA-000000000-0":
            return IdentityResult(verified=False, message="No record found for this card.")
        return IdentityResult(verified=True, full_name=f"TEST PERSON {card_number[-6:-2]}")

    def verify_liveness(self, selfie, card_number):
        if not selfie or len(selfie) < 1024:
            return LivenessResult(passed=False, message="Image too small or empty.")
        if b"FAIL" in selfie[:64]:
            return LivenessResult(passed=False, match_score=0.21,
                                  message="Face does not match the ID photo.")
        return LivenessResult(passed=True, match_score=0.97, provider_ref="mock-live-1")
