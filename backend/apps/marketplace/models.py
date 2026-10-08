"""
Financial-services marketplace: partner insurers and lenders, their products, and the
applications customers send them through SokoPay.

SokoPay is the distribution channel, not the underwriter or lender. Every application
records the customer's explicit consent to share their details with that partner
(Data Protection Act, 2012 — Act 843).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Category(models.TextChoices):
    """Kinds of partner product SokoPay aggregates. SokoPay never runs these itself:
    each is offered by a partner licensed by the relevant regulator."""

    INSURANCE = "insurance", "Insurance"      # NIC
    LENDING = "lending", "Loans"              # BoG
    SAVINGS = "savings", "Savings"            # BoG (banks, savings & loans)
    INVESTMENT = "investment", "Investments"  # SEC Ghana
    PENSION = "pension", "Pensions"           # NPRA


class FinancialProvider(TimeStampedModel):
    key = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=12, choices=Category.choices)
    regulator = models.CharField(max_length=150)          # e.g. "NIC", "Bank of Ghana"
    licence_number = models.CharField(max_length=64, blank=True)
    connector = models.CharField(max_length=48)           # key in connectors.registry FINANCIAL
    is_active = models.BooleanField(default=False)        # off until agreement + due diligence

    class Meta:
        db_table = "marketplace_provider"

    def __str__(self) -> str:
        return self.name


class FinancialProduct(TimeStampedModel):
    provider = models.ForeignKey(FinancialProvider, on_delete=models.PROTECT,
                                 related_name="products")
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=12, choices=Category.choices)
    summary = models.TextField(blank=True)
    min_minor = models.BigIntegerField(null=True, blank=True)
    max_minor = models.BigIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "marketplace_product"

    def __str__(self) -> str:
        return f"{self.name} ({self.provider})"


class ProductApplication(TimeStampedModel):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted to partner"
        PENDING = "pending", "Under review by partner"
        APPROVED = "approved", "Approved by partner"
        DECLINED = "declined", "Declined by partner"
        FAILED = "failed", "Could not be sent"

    reference = models.CharField(max_length=16, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="product_applications")
    product = models.ForeignKey(FinancialProduct, on_delete=models.PROTECT,
                                related_name="applications")
    amount_minor = models.BigIntegerField(null=True, blank=True)  # cover / loan amount
    status = models.CharField(max_length=10, choices=Status.choices)
    partner_ref = models.CharField(max_length=64, blank=True)
    message = models.CharField(max_length=255, blank=True)
    consent_given_at = models.DateTimeField()                     # explicit data-sharing consent

    class Meta:
        db_table = "marketplace_application"
        indexes = [models.Index(fields=["user", "created_at"])]


class ProductTransaction(TimeStampedModel):
    """
    Money moving between a customer's wallet and a financial partner (DEMI):
    an insurance premium paid from the wallet, or a loan disbursed into it.
    """

    class Kind(models.TextChoices):
        PREMIUM = "premium", "Premium paid from wallet"
        DISBURSEMENT = "disbursement", "Loan disbursed to wallet"
        CONTRIBUTION = "contribution", "Paid in from wallet"
        WITHDRAWAL = "withdrawal", "Paid out to wallet"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        REQUESTED = "requested", "Requested, waiting for the provider"
        REJECTED = "rejected", "Rejected by the provider"

    application = models.ForeignKey(ProductApplication, on_delete=models.PROTECT,
                                    related_name="transactions")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount_minor = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)
    # The partner's reference for a disbursement — makes their retries idempotent.
    partner_ref = models.CharField(max_length=64, unique=True, null=True, blank=True)

    class Meta:
        db_table = "marketplace_product_txn"
