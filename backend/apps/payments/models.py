"""
Payments domain models.

A `Payment` is the record of one customer payment (a bill, an airtime/data top-up,
and later a merchant checkout). A `Biller` is something we can pay (ECG, Ghana Water,
a telco for airtime). A `RailEvent` is the stored, audited record of every inbound
callback from a rail — something the old codebase never kept, which made disputes
impossible to investigate.

The money effects of a payment live in the ledger (apps.ledger), not here. This model
holds the operational state and links to the ledger by reference.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Biller(TimeStampedModel):
    class Category(models.TextChoices):
        ELECTRICITY = "electricity", "Electricity"
        WATER = "water", "Water"
        TV = "tv", "TV"
        AIRTIME = "airtime", "Airtime"
        DATA = "data", "Data"
        INTERNET = "internet", "Internet"
        EDUCATION = "education", "Education"

    code = models.CharField(max_length=32, unique=True)          # our code, e.g. "ECG_PREPAID"
    name = models.CharField(max_length=128)
    category = models.CharField(max_length=16, choices=Category.choices)
    rail_biller_code = models.CharField(max_length=64)           # code the rail expects
    network = models.CharField(max_length=16, blank=True)        # for airtime/data billers
    # Which connector delivers payments to this biller: "rail" (through the Enhanced
    # PSP partner — the default) or a direct-integration key such as "ecg" once that
    # integration is live. See apps.connectors.registry.
    connector = models.CharField(max_length=32, default="rail")
    supports_lookup = models.BooleanField(default=False)         # can we look up the account name?
    min_minor = models.BigIntegerField(default=100)              # GH₵1.00
    max_minor = models.BigIntegerField(default=100_000_00)       # safety ceiling
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "payments_biller"
        ordering = ("category", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Payment(TimeStampedModel):
    class Purpose(models.TextChoices):
        BILL = "bill", "Bill payment"
        AIRTIME = "airtime", "Airtime"
        DATA = "data", "Data"
        MERCHANT = "merchant", "Merchant payment"
        WALLET_FUND = "wallet_fund", "Wallet funding"   # DEMI: top up a customer wallet

    class Status(models.TextChoices):
        CREATED = "created", "Created"        # record made, not yet sent to rail
        PENDING = "pending", "Pending"        # at the rail, awaiting approval/processing
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class Mode(models.TextChoices):
        LIVE = "live", "Live"
        TEST = "test", "Test"

    class Source(models.TextChoices):
        MOMO = "momo", "Mobile money (collected via the rail)"
        WALLET = "wallet", "SokoPay wallet (DEMI)"

    reference = models.CharField(max_length=16, unique=True, db_index=True)  # SP-...
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="payments", null=True, blank=True,
    )
    biller = models.ForeignKey(Biller, on_delete=models.PROTECT, null=True, blank=True)
    product_code = models.CharField(max_length=64, blank=True)    # e.g. a data bundle code
    delivery_token = models.CharField(max_length=128, blank=True)  # e.g. an ECG prepaid token
    # Set for merchant checkout payments (purpose == MERCHANT). String FK avoids an
    # import cycle between payments and merchants.
    merchant = models.ForeignKey(
        "merchants.Merchant", on_delete=models.PROTECT, null=True, blank=True,
        related_name="payments",
    )
    account_ref = models.CharField(max_length=64, blank=True)   # meter/smartcard/phone being paid

    # Money, all in minor units (pesewas). amount = value to the biller/merchant;
    # fee = SokoPay's charge; total = what the payer is debited (amount + fee).
    amount_minor = models.BigIntegerField()
    fee_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="GHS")

    funding_source = models.CharField(max_length=8, choices=Source.choices, default=Source.MOMO)
    network = models.CharField(max_length=16)                   # mtn/telecel/at/card, or "wallet"
    payer = models.CharField(max_length=32, blank=True)         # E.164 phone; masked in API output

    rail = models.CharField(max_length=16)
    rail_ref = models.CharField(max_length=64, blank=True, db_index=True)

    failure_code = models.CharField(max_length=32, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    mode = models.CharField(max_length=4, choices=Mode.choices, default=Mode.LIVE)
    # Where an online payment started: "link:<token>", "session:<token>", "qr:<token>",
    # "shop:<code>" (hosted checkout pages). Blank for app / API payments.
    source_ref = models.CharField(max_length=48, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments_payment"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["purpose", "status"]),
            # Merchant payment history (portal + merchant app), newest first.
            models.Index(fields=["merchant", "mode", "created_at"]),
            # Customer activity feed (apps.activity), newest first.
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.purpose} {self.status}"

    @property
    def is_terminal(self) -> bool:
        return self.status in (self.Status.SUCCEEDED, self.Status.FAILED, self.Status.REFUNDED)

    @property
    def payer_masked(self) -> str:
        """Mask the payer phone for display/API, e.g. +23324•••8519."""
        p = self.payer
        if len(p) >= 7:
            return f"{p[:6]}•••{p[-4:]}"
        return "•••"


class RailEvent(TimeStampedModel):
    """Append-only record of every inbound rail webhook, for audit and dispute handling."""

    rail = models.CharField(max_length=16)
    provider_ref = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=16)
    signature_ok = models.BooleanField()
    payload = models.JSONField(default=dict)
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, null=True, blank=True, related_name="rail_events"
    )

    class Meta:
        db_table = "payments_rail_event"

    def __str__(self) -> str:
        return f"{self.rail}:{self.provider_ref} {self.status} sig_ok={self.signature_ok}"
