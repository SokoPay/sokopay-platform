"""Wallet models. Balances live in the ledger; this tracks outbound interop transfers."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class ExternalTransfer(TimeStampedModel):
    """A transfer from a SokoPay wallet to another institution (MoMo, bank, fintech)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed (refunded to wallet)"

    reference = models.CharField(max_length=16, unique=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                               related_name="external_transfers")
    destination_type = models.CharField(max_length=8)        # momo | bank | wallet
    institution = models.CharField(max_length=32)            # mtn / bank code / zeepay
    account = models.CharField(max_length=34)                # phone or account number
    account_name = models.CharField(max_length=128, blank=True)  # from name enquiry
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="GHS")
    narrative = models.CharField(max_length=140, blank=True)

    connector = models.CharField(max_length=32)              # route used, e.g. "rail"
    provider_ref = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    failure_code = models.CharField(max_length=32, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wallet_external_transfer"
        indexes = [models.Index(fields=["status", "created_at"]),
                   models.Index(fields=["sender", "created_at"])]   # activity feed

    def __str__(self) -> str:
        return f"{self.reference} {self.destination_type}:{self.institution} {self.status}"


class InboundRemittance(TimeStampedModel):
    """An international remittance delivered into a SokoPay wallet (DEMI)."""

    class Status(models.TextChoices):
        CREDITED = "credited", "Credited to wallet"
        REJECTED = "rejected", "Rejected (partner returns it to the sender)"

    partner = models.CharField(max_length=32)
    partner_ref = models.CharField(max_length=64)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  null=True, blank=True, related_name="inbound_remittances")
    recipient_phone = models.CharField(max_length=16)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    sender_name = models.CharField(max_length=150, blank=True)
    sender_country = models.CharField(max_length=2, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "wallet_inbound_remittance"
        constraints = [
            models.UniqueConstraint(fields=["partner", "partner_ref"], name="uniq_remittance_ref"),
        ]


class WalletNumber(models.Model):
    """
    A customer's SokoPay wallet ID — what they share to receive money or to cash in at
    an agent, instead of (or as well as) their phone number.

    Format: 10 digits starting with 7, the last a Luhn check digit. Starting with 7 means
    it can never be confused with a Ghana phone number (0XXXXXXXXX / +233…); the check
    digit catches typos before any money moves. Assigned on first use, never reused.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name="wallet_number")
    number = models.CharField(max_length=10, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wallet_number"

    def __str__(self) -> str:
        return f"{self.number} ({self.user_id})"


class SavedRecipient(models.Model):
    """
    A customer's favourite: a SokoPay user, a mobile-money number, a bank account,
    another wallet, or a merchant. Saved only after a successful lookup, so a typo is
    never stored. Purely a convenience: every payment still re-checks the destination.
    """

    class Kind(models.TextChoices):
        SOKOPAY = "sokopay", "SokoPay user"
        MOMO = "momo", "Mobile money"
        BANK = "bank", "Bank account"
        WALLET = "wallet", "Other wallet"
        MERCHANT = "merchant", "Merchant"

    id = models.BigAutoField(primary_key=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_recipients")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    label = models.CharField(max_length=80)                  # what the customer sees
    value = models.CharField(max_length=34)                  # phone / wallet ID / account no / merchant code
    institution = models.CharField(max_length=32, blank=True)  # network or bank code
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wallet_saved_recipient"
        constraints = [models.UniqueConstraint(fields=["owner", "kind", "value", "institution"],
                                               name="uniq_saved_recipient")]
        indexes = [models.Index(fields=["owner", "kind"])]


class CrossBorderTransfer(TimeStampedModel):
    """A send from a SokoPay wallet to another country through a licensed partner."""

    class Status(models.TextChoices):
        PENDING = "pending", "On its way"
        SUCCEEDED = "succeeded", "Delivered"
        FAILED = "failed", "Failed, refunded"

    reference = models.CharField(max_length=16, unique=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cross_border_sends")
    provider = models.CharField(max_length=32)
    quote_id = models.CharField(max_length=64)
    country = models.CharField(max_length=2)
    method = models.CharField(max_length=16)                  # mobile_money | bank
    account = models.CharField(max_length=34)
    institution = models.CharField(max_length=32)
    recipient_name = models.CharField(max_length=120)
    purpose = models.CharField(max_length=32)
    amount_minor = models.BigIntegerField()                   # GHS sent before the fee
    fee_minor = models.BigIntegerField()
    total_minor = models.BigIntegerField()                    # debited from the wallet
    receive_amount = models.CharField(max_length=24)
    receive_currency = models.CharField(max_length=3)
    rate = models.CharField(max_length=24)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_ref = models.CharField(max_length=64, blank=True, db_index=True)
    failure_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wallet_cross_border_transfer"
        indexes = [models.Index(fields=["sender", "created_at"]), models.Index(fields=["status", "updated_at"])]


class LifestyleOrder(TimeStampedModel):
    """A ticket or food order paid from the wallet; the partner fulfils it."""

    class Status(models.TextChoices):
        PENDING = "pending", "Processing"
        SUCCEEDED = "succeeded", "Confirmed"
        FAILED = "failed", "Failed, refunded"

    reference = models.CharField(max_length=16, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lifestyle_orders")
    category = models.CharField(max_length=12)                 # ticketing | food
    partner = models.CharField(max_length=48)
    offering_code = models.CharField(max_length=64)
    offering_name = models.CharField(max_length=150)
    quantity = models.PositiveSmallIntegerField()
    unit_price_minor = models.BigIntegerField()
    total_minor = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_ref = models.CharField(max_length=64, blank=True, db_index=True)
    token = models.CharField(max_length=128, blank=True)       # e-ticket code
    failure_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wallet_lifestyle_order"
        indexes = [models.Index(fields=["user", "created_at"]), models.Index(fields=["status", "updated_at"])]
