"""
Bulk payouts: a merchant pays many recipients (staff, suppliers) from their SokoPay
balance in one go, from an uploaded spreadsheet.

Lifecycle of a batch:
    DRAFT ──submit──▶ AWAITING_APPROVAL ──approve──▶ PROCESSING ──▶ COMPLETED
      │                     │                                     └▶ COMPLETED_WITH_FAILURES
      └──cancel──▶ CANCELLED└──reject──▶ REJECTED

Money is reserved from the merchant's balance at approval and then released item by
item — to a SokoPay wallet, to another institution via its connector, or back to the
merchant if an item fails. The batch is the audit record; the ledger is the money.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class BulkPayout(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft — review rows"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        PROCESSING = "processing", "Paying recipients"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_FAILURES = "completed_with_failures", "Completed — some failed (refunded)"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    merchant = models.ForeignKey("merchants.Merchant", on_delete=models.PROTECT,
                                 related_name="bulk_payouts")
    note = models.CharField(max_length=120, blank=True)          # e.g. "October payroll"
    source_filename = models.CharField(max_length=200)
    file_sha256 = models.CharField(max_length=64, db_index=True)  # catches re-uploads

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    exclude_invalid = models.BooleanField(default=False)

    total_count = models.IntegerField(default=0)
    valid_count = models.IntegerField(default=0)
    invalid_count = models.IntegerField(default=0)
    total_amount_minor = models.BigIntegerField(default=0)      # valid rows
    total_fee_minor = models.BigIntegerField(default=0)         # valid rows
    reserved_minor = models.BigIntegerField(default=0)          # taken from the merchant
    paid_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="bulk_payouts_created")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name="bulk_payouts_approved")
    self_approved = models.BooleanField(default=False)          # sole approver = maker
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bulk_payout"
        indexes = [models.Index(fields=["merchant", "status", "created_at"])]

    def __str__(self) -> str:
        return f"Bulk {self.note or self.source_filename} ({self.status})"

    @property
    def needed_minor(self) -> int:
        return self.total_amount_minor + self.total_fee_minor

    @property
    def is_final(self) -> bool:
        return self.status in (self.Status.COMPLETED, self.Status.COMPLETED_WITH_FAILURES,
                               self.Status.REJECTED, self.Status.CANCELLED)


class BulkPayoutItem(TimeStampedModel):
    class Destination(models.TextChoices):
        SOKOPAY = "sokopay", "SokoPay wallet"
        MOMO = "momo", "Mobile money"
        BANK = "bank", "Bank account"
        WALLET = "wallet", "Other fintech wallet"

    class Status(models.TextChoices):
        INVALID = "invalid", "Invalid — will not be paid"
        VALID = "valid", "Ready"
        SKIPPED = "skipped", "Skipped (invalid row excluded)"
        PROCESSING = "processing", "Sent — awaiting confirmation"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed — refunded to merchant"

    batch = models.ForeignKey(BulkPayout, on_delete=models.CASCADE, related_name="items")
    row_number = models.IntegerField()                           # 1-based data row in the file

    recipient_name = models.CharField(max_length=120)
    destination_type = models.CharField(max_length=8, choices=Destination.choices, blank=True)
    institution = models.CharField(max_length=32, blank=True)    # mtn / GCB / zeepay …
    account = models.CharField(max_length=34, blank=True)        # +233… or account number
    amount_minor = models.BigIntegerField(default=0)
    fee_minor = models.BigIntegerField(default=0)
    narrative = models.CharField(max_length=140, blank=True)

    status = models.CharField(max_length=12, choices=Status.choices)
    error = models.CharField(max_length=255, blank=True)
    connector = models.CharField(max_length=32, blank=True)
    provider_ref = models.CharField(max_length=64, blank=True, db_index=True)
    recipient_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                       null=True, blank=True, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bulk_payout_item"
        ordering = ("row_number",)
        indexes = [models.Index(fields=["batch", "status"])]

    def __str__(self) -> str:
        return f"Row {self.row_number}: {self.recipient_name} {self.amount_minor} ({self.status})"

    @property
    def total_minor(self) -> int:
        return self.amount_minor + self.fee_minor
