"""
Merchant domain models.

Covers onboarding (KYB), team members and roles, settlement destinations, API keys,
uploaded documents, and settlement records with a maker-checker step. The money a
merchant is owed lives in the ledger (merchant_payable:<id>); these models hold the
operational and compliance state around it.
"""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import models

from apps.common.encryption import EncryptedCharField
from apps.common.models import TimeStampedModel

# Default merchant pricing. Real plans become a FeePlan table later; these are the
# launch defaults. [VERIFY against the Enhanced PSP's cost]
DEFAULT_MDR_BP = 150          # 1.50%
DEFAULT_MDR_CAP_MINOR = 5_000  # GH₵50.00 cap per transaction


class Merchant(TimeStampedModel):
    class BusinessType(models.TextChoices):
        REGISTERED = "registered", "Registered business"
        SOLE_TRADER = "sole_trader", "Sole proprietor / individual"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    class RiskTier(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    legal_name = models.CharField(max_length=200)
    trading_name = models.CharField(max_length=200, blank=True)
    business_type = models.CharField(max_length=16, choices=BusinessType.choices)
    category = models.CharField(max_length=64, blank=True)       # merchant category
    tin = models.CharField(max_length=32, blank=True)            # tax ID (registered)
    registration_no = models.CharField(max_length=64, blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_merchants"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    risk_tier = models.CharField(max_length=8, choices=RiskTier.choices, default=RiskTier.MEDIUM)
    rejection_reason = models.CharField(max_length=255, blank=True)

    # Pricing
    mdr_bp = models.PositiveIntegerField(default=DEFAULT_MDR_BP)
    mdr_cap_minor = models.BigIntegerField(default=DEFAULT_MDR_CAP_MINOR)

    # Printed on the merchant's static QR sticker and typed in by customers who can't
    # scan. Short, unambiguous (Crockford base32), assigned on creation.
    short_code = models.CharField(max_length=8, unique=True, null=True, blank=True)

    class Meta:
        db_table = "merchants_merchant"
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return f"{self.trading_name or self.legal_name} ({self.status})"

    @property
    def is_live(self) -> bool:
        """Approved merchants can take real (live-mode) payments."""
        return self.status == self.Status.APPROVED


class MerchantMember(TimeStampedModel):
    """A user's membership of a merchant, with a role that scopes what they can do."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        FINANCE = "finance", "Finance"
        CASHIER = "cashier", "Cashier"
        DEVELOPER = "developer", "Developer"

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="merchant_memberships")
    role = models.CharField(max_length=16, choices=Role.choices)

    class Meta:
        db_table = "merchants_member"
        unique_together = ("merchant", "user")

    def __str__(self) -> str:
        return f"{self.user} @ {self.merchant} as {self.role}"


class SettlementAccount(TimeStampedModel):
    """Where a merchant is paid out (MoMo or bank)."""

    class Kind(models.TextChoices):
        MOMO = "momo", "Mobile money"
        BANK = "bank", "Bank account"

    class NameCheck(models.TextChoices):
        PENDING = "pending", "Pending"
        MATCHED = "matched", "Matched"
        MISMATCH = "mismatch", "Mismatch"

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="settlement_accounts")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    provider = models.CharField(max_length=32)                 # network or bank name
    account_no = models.CharField(max_length=32)               # phone (MoMo) or account number
    account_name = models.CharField(max_length=128)
    name_check_status = models.CharField(max_length=8, choices=NameCheck.choices,
                                         default=NameCheck.PENDING)
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "merchants_settlement_account"

    def __str__(self) -> str:
        return f"{self.kind}:{self.account_no} ({self.merchant_id})"


class ApiKey(TimeStampedModel):
    """
    A merchant API credential. We store only a SHA-256 hash of the secret, never the
    secret itself (it is shown once at creation). The prefix lets us look a key up
    without storing it. Keys are high-entropy, so SHA-256 is appropriate (Argon2 is
    for low-entropy human passwords).
    """

    class Mode(models.TextChoices):
        TEST = "test", "Test"
        LIVE = "live", "Live"

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="api_keys")
    mode = models.CharField(max_length=4, choices=Mode.choices)
    prefix = models.CharField(max_length=24, unique=True, db_index=True)
    key_hash = models.CharField(max_length=64)                  # sha256 hex
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "merchants_api_key"

    def __str__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"{self.prefix}… ({self.mode}, {state})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()


class MerchantDocument(TimeStampedModel):
    """A KYB document reference (the file itself lives in object storage, later)."""

    class Kind(models.TextChoices):
        OWNER_ID = "owner_id", "Owner Ghana Card"
        REGISTRATION = "registration", "Business registration"
        TIN = "tin", "TIN certificate"
        PREMISES = "premises", "Photo of premises"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    storage_key = models.CharField(max_length=256, blank=True)  # S3 key, later
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.UPLOADED)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "merchants_document"


class Settlement(TimeStampedModel):
    """
    A payout of a merchant's available balance to a settlement account.

    Maker-checker: a settlement over the approval threshold, or to a destination
    changed very recently, is created in AWAITING_APPROVAL and only executed after a
    different staff user approves it. The maker can never approve their own request.
    """

    class Status(models.TextChoices):
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="settlements")
    destination = models.ForeignKey(SettlementAccount, on_delete=models.PROTECT)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="GHS")
    status = models.CharField(max_length=20, choices=Status.choices)
    rail = models.CharField(max_length=16, blank=True)       # which partner carried it
    rail_ref = models.CharField(max_length=64, blank=True, db_index=True)
    failure_code = models.CharField(max_length=64, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="settlements_requested"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="settlements_approved",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "merchants_settlement"
        indexes = [models.Index(fields=["merchant", "status"])]

    def __str__(self) -> str:
        return f"Settlement {self.amount_minor} to {self.merchant_id} ({self.status})"


class PaymentRequest(TimeStampedModel):
    """
    A dynamic QR / payment link: "pay this merchant GH₵X for Y". Shown on the
    merchant's screen or sent to the customer; expires after a short window.
    An open-amount request (amount_minor NULL) lets the customer enter the amount.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Awaiting payment"
        PAID = "paid", "Paid"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="payment_requests")
    token = models.CharField(max_length=32, unique=True)            # what the QR carries
    amount_minor = models.BigIntegerField(null=True, blank=True)    # NULL = customer enters it
    description = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   null=True, blank=True, related_name="+")
    payment = models.ForeignKey("payments.Payment", on_delete=models.PROTECT,
                                null=True, blank=True, related_name="+")

    class Meta:
        db_table = "merchants_payment_request"
        indexes = [models.Index(fields=["merchant", "status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.merchant} request {self.token} ({self.status})"


class Refund(TimeStampedModel):
    """
    Money going back to the customer for a merchant payment, paid from the merchant's
    SokoPay balance. Always to the ORIGINAL payer by the ORIGINAL method: a wallet
    payment is credited back instantly; a MoMo payment is sent out through the rail
    and tracked like a settlement (processing until the partner confirms). The
    merchant fee (MDR) is not returned.
    """

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Refunded"
        FAILED = "failed", "Failed, money back in the merchant balance"

    class Destination(models.TextChoices):
        WALLET = "wallet", "SokoPay wallet"
        MOMO = "momo", "Mobile money"

    payment = models.ForeignKey("payments.Payment", on_delete=models.PROTECT, related_name="refunds")
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="refunds")
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="GHS")
    reason = models.CharField(max_length=255)
    destination = models.CharField(max_length=8, choices=Destination.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PROCESSING)
    rail = models.CharField(max_length=16, blank=True)
    rail_ref = models.CharField(max_length=64, blank=True, db_index=True)
    failure_code = models.CharField(max_length=64, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    dispute = models.ForeignKey("Dispute", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="refunds")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "merchants_refund"
        indexes = [models.Index(fields=["merchant", "created_at"]),
                   models.Index(fields=["status", "updated_at"])]

    def __str__(self) -> str:
        return f"Refund {self.amount_minor} on {self.payment_id} ({self.status})"


class Dispute(TimeStampedModel):
    """
    A customer says something is wrong with a merchant payment. The merchant answers
    (refund, or explain); if they don't settle it, SokoPay decides. While a dispute is
    open the disputed amount is held back from the merchant's settlements.
    """

    class Reason(models.TextChoices):
        NOT_RECEIVED = "not_received", "I didn't get what I paid for"
        NOT_AS_DESCRIBED = "not_as_described", "Not as described / faulty"
        WRONG_AMOUNT = "wrong_amount", "I was charged the wrong amount"
        DUPLICATE = "duplicate", "I was charged twice"
        UNAUTHORISED = "unauthorised", "I didn't make this payment"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Waiting for the merchant"
        RESPONDED = "responded", "Merchant answered, SokoPay reviewing"
        RESOLVED_CUSTOMER = "resolved_customer", "Resolved, refunded"
        RESOLVED_MERCHANT = "resolved_merchant", "Resolved in the merchant's favour"
        WITHDRAWN = "withdrawn", "Withdrawn by the customer"

    ACTIVE = ("open", "responded")

    payment = models.ForeignKey("payments.Payment", on_delete=models.PROTECT, related_name="disputes")
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="disputes")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="disputes")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    description = models.CharField(max_length=1000)
    amount_minor = models.BigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    respond_by = models.DateTimeField()
    merchant_response = models.CharField(max_length=1000, blank=True)
    responded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="+")
    responded_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=1000, blank=True)

    class Meta:
        db_table = "merchants_dispute"
        constraints = [models.UniqueConstraint(fields=["payment"], condition=models.Q(status__in=("open", "responded")),
                                               name="one_active_dispute_per_payment")]
        indexes = [models.Index(fields=["merchant", "status"]), models.Index(fields=["status", "respond_by"])]

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE


class WebhookEndpoint(TimeStampedModel):
    """A merchant server URL that receives signed event callbacks (see webhooks.py)."""

    EVENTS = ("payment.succeeded", "payment.failed", "refund.succeeded", "refund.failed",
              "settlement.paid", "settlement.failed", "dispute.opened", "dispute.resolved")

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="webhook_endpoints")
    url = models.URLField(max_length=300)
    secret = EncryptedCharField()                     # signing secret, encrypted at rest
    events = models.JSONField(default=list)           # empty = all events
    active = models.BooleanField(default=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    disabled_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "merchants_webhook_endpoint"

    def wants(self, event: str) -> bool:
        return self.active and (not self.events or event in self.events)


class WebhookDelivery(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Delivered"
        FAILED = "failed", "Retrying"
        ABANDONED = "abandoned", "Gave up"

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event_id = models.CharField(max_length=40, db_index=True)
    event_type = models.CharField(max_length=32)
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "merchants_webhook_delivery"
        indexes = [models.Index(fields=["status", "next_attempt_at"]),
                   models.Index(fields=["endpoint", "created_at"])]



class PaymentLink(TimeStampedModel):
    """A reusable link a merchant shares (WhatsApp, Instagram, invoice) to get paid online."""

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="payment_links")
    token = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=240, blank=True)
    amount_minor = models.BigIntegerField(null=True, blank=True)    # NULL = payer enters the amount
    active = models.BooleanField(default=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)   # NULL = unlimited
    uses = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "merchants_payment_link"


class CheckoutSession(TimeStampedModel):
    """A one-off hosted checkout a merchant's website creates by API, then redirects to."""

    class Status(models.TextChoices):
        OPEN = "open", "Awaiting payment"
        PAID = "paid", "Paid"
        EXPIRED = "expired", "Expired"

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name="checkout_sessions")
    token = models.CharField(max_length=32, unique=True)
    amount_minor = models.BigIntegerField()
    description = models.CharField(max_length=120, blank=True)
    merchant_reference = models.CharField(max_length=64, blank=True)   # their order id
    success_url = models.URLField(max_length=300, blank=True)
    cancel_url = models.URLField(max_length=300, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    mode = models.CharField(max_length=4, default="live")
    expires_at = models.DateTimeField()
    payment = models.ForeignKey("payments.Payment", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")

    class Meta:
        db_table = "merchants_checkout_session"
        indexes = [models.Index(fields=["merchant", "merchant_reference"])]
