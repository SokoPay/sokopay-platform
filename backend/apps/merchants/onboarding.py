"""
Merchant onboarding (KYB) services and the status machine.

Onboarding itself is licence-independent (collecting who a business is). The money
activities a merchant can then do ARE licence-gated (see checkout.py / settlement.py).

Status flow:
    draft → submitted → in_review → approved
                                  ↘ rejected (→ can resubmit → submitted)
    approved → suspended (and back to approved on reinstatement)
"""

from __future__ import annotations

from django.db import transaction

from .exceptions import InvalidTransition
from .models import Merchant, MerchantMember

# Allowed status transitions.
_ALLOWED = {
    Merchant.Status.DRAFT: {Merchant.Status.SUBMITTED},
    Merchant.Status.SUBMITTED: {Merchant.Status.IN_REVIEW, Merchant.Status.REJECTED},
    Merchant.Status.IN_REVIEW: {Merchant.Status.APPROVED, Merchant.Status.REJECTED},
    Merchant.Status.REJECTED: {Merchant.Status.SUBMITTED},
    Merchant.Status.APPROVED: {Merchant.Status.SUSPENDED},
    Merchant.Status.SUSPENDED: {Merchant.Status.APPROVED},
}


def _transition(merchant: Merchant, to_status: str) -> None:
    allowed = _ALLOWED.get(Merchant.Status(merchant.status), set())
    if to_status not in allowed:
        raise InvalidTransition(f"Cannot move merchant from {merchant.status} to {to_status}.")
    merchant.status = to_status


@transaction.atomic
def create_merchant(*, owner, legal_name, business_type, trading_name="", category="",
                    tin="", registration_no="") -> Merchant:
    """Create a merchant in DRAFT and make the owner its first member (role OWNER)."""
    merchant = Merchant.objects.create(
        owner=owner,
        legal_name=legal_name,
        trading_name=trading_name,
        business_type=business_type,
        category=category,
        tin=tin,
        registration_no=registration_no,
        status=Merchant.Status.DRAFT,
    )
    MerchantMember.objects.create(merchant=merchant, user=owner, role=MerchantMember.Role.OWNER)
    from .qr import ensure_short_code   # local import: qr imports models from this app
    ensure_short_code(merchant)
    return merchant


def submit_for_review(merchant: Merchant) -> Merchant:
    """Merchant submits their KYB. Moves draft/rejected → submitted."""
    _transition(merchant, Merchant.Status.SUBMITTED)
    merchant.rejection_reason = ""
    merchant.save(update_fields=["status", "rejection_reason", "updated_at"])
    # AML: screen the business names before a reviewer looks at it.
    from apps.compliance.screening import screen_merchant
    screen_merchant(merchant)
    return merchant


def begin_review(merchant: Merchant) -> Merchant:
    """A staff user picks up the application for review."""
    _transition(merchant, Merchant.Status.IN_REVIEW)
    merchant.save(update_fields=["status", "updated_at"])
    return merchant


def approve(merchant: Merchant, *, risk_tier: str = Merchant.RiskTier.MEDIUM) -> Merchant:
    """Approve a merchant so they can transact (subject to licence capabilities)."""
    _transition(merchant, Merchant.Status.APPROVED)
    merchant.risk_tier = risk_tier
    merchant.save(update_fields=["status", "risk_tier", "updated_at"])
    return merchant


def reject(merchant: Merchant, *, reason: str) -> Merchant:
    _transition(merchant, Merchant.Status.REJECTED)
    merchant.rejection_reason = reason
    merchant.save(update_fields=["status", "rejection_reason", "updated_at"])
    return merchant


def suspend(merchant: Merchant, *, reason: str = "") -> Merchant:
    _transition(merchant, Merchant.Status.SUSPENDED)
    merchant.rejection_reason = reason
    merchant.save(update_fields=["status", "rejection_reason", "updated_at"])
    return merchant
