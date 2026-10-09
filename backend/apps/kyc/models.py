"""
KYC profile per customer: verification tier, the (encrypted) Ghana Card on file, and
compliance holds.

The Ghana Card number is encrypted at rest; `ghana_card_hash` (a keyed HMAC) lets us
enforce "one card, one SokoPay identity" without storing the number in the clear.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.encryption import EncryptedCharField
from apps.common.models import TimeStampedModel


class KycProfile(TimeStampedModel):
    class Tier(models.IntegerChoices):
        # Names follow the Bank of Ghana mobile-money account tiers.
        BASIC = 0, "Minimum (phone verified)"
        VERIFIED = 1, "Medium (Ghana Card verified)"
        ENHANCED = 2, "Enhanced (Ghana Card + selfie)"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="kyc")
    tier = models.PositiveSmallIntegerField(choices=Tier.choices, default=Tier.BASIC)

    ghana_card_number = EncryptedCharField(blank=True)
    ghana_card_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    verified_name = models.CharField(max_length=150, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    # Tier 2: a live selfie matched to the Ghana Card photo. We keep only the outcome —
    # never the image.
    liveness_verified_at = models.DateTimeField(null=True, blank=True)
    liveness_score = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    liveness_provider_ref = models.CharField(max_length=64, blank=True)

    # Compliance hold: blocks money leaving the wallet (credits still allowed).
    frozen = models.BooleanField(default=False)
    frozen_reason = models.CharField(max_length=255, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  null=True, blank=True, related_name="+")

    class Meta:
        db_table = "kyc_profile"
        verbose_name = "KYC profile"

    def __str__(self) -> str:
        return f"{self.user} — tier {self.tier}{' (frozen)' if self.frozen else ''}"

    @property
    def ghana_card_masked(self) -> str:
        n = self.ghana_card_number or ""
        return f"GHA-•••••{n[-6:]}" if n else ""


class IdentityDocument(TimeStampedModel):
    """
    An identity document the customer adds from their profile: Ghana Card, passport or
    driver's licence, with photos.

    * Ghana Card: the number is checked with NIA (the same check as the tier-1 upgrade),
      so the document is VERIFIED immediately when the check passes.
    * Passport / driver's licence: PENDING until a compliance officer compares the photo
      with the details and accepts or rejects it in the back office. They're kept as
      supporting identity; the Ghana Card stays the document that raises wallet limits
      for Ghanaians (Bank of Ghana).

    The number is encrypted at rest (only its last characters are kept in clear for
    display); photos are encrypted files (apps.common.secure_files).
    """

    class Type(models.TextChoices):
        GHANA_CARD = "ghana_card", "Ghana Card"
        PASSPORT = "passport", "Passport"
        DRIVERS_LICENCE = "drivers_licence", "Driver's licence"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Not accepted"
        REPLACED = "replaced", "Replaced by a newer upload"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="identity_documents")
    doc_type = models.CharField(max_length=16, choices=Type.choices)
    number = EncryptedCharField(blank=True)
    number_last4 = models.CharField(max_length=4, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    front_key = models.CharField(max_length=256)
    back_key = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    review_note = models.CharField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "kyc_identity_document"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "created_at"]), models.Index(fields=["user", "doc_type"])]

    def __str__(self) -> str:
        return f"{self.get_doc_type_display()} ••{self.number_last4} ({self.status})"
