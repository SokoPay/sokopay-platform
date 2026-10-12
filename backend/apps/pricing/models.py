"""
Configurable prices: what SokoPay charges (fees) and what it pays agents (commissions),
plus time-limited discounts on fees (promotions).

A PriceRule is never edited once approved. To change a price, propose a new rule with a
later `effective_from`; a second staff member (finance) approves it. The rule in force
for a product at a moment is the most recent APPROVED rule whose effective_from has
passed. With no approved rule, the code defaults in services.DEFAULTS apply.

amount = flat_minor + amount × percent_bp / 10 000 (half-up), then clamped to
[min_minor, max_minor] (max 0 = no cap) and never more than the transaction amount.
So a price can be free (all zero), a flat amount, a percentage capped at a figure, or both.

A Promotion takes a % off (or all of) a fee between two moments, for one product or all
of them. It goes through the same two-person approval. When several apply, the customer
gets the biggest discount.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class PriceRule(TimeStampedModel):
    class Kind(models.TextChoices):
        FEE = "fee", "Fee charged by SokoPay"
        COMMISSION = "commission", "Commission paid to agents"

    class Product(models.TextChoices):
        P2P = "p2p", "Send to a SokoPay user (sender pays on top)"
        TRANSFER_MOMO = "transfer_momo", "Send to mobile money / other wallets (sender pays on top)"
        TRANSFER_BANK = "transfer_bank", "Send to a bank account (sender pays on top)"
        BILL = "bill", "Bill payment (customer pays on top)"
        AIRTIME = "airtime", "Airtime (customer pays on top)"
        DATA = "data", "Data bundle (customer pays on top)"
        CASH_OUT = "cash_out", "Agent cash-out (customer pays on top)"
        CASH_IN = "cash_in", "Agent cash-in"
        BULK_MOMO = "bulk_momo", "Bulk payout to mobile money (merchant pays)"
        BULK_BANK = "bulk_bank", "Bulk payout to bank (merchant pays)"

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    product = models.CharField(max_length=16, choices=Product.choices)
    flat_minor = models.PositiveIntegerField(default=0)
    percent_bp = models.PositiveIntegerField(default=0)        # 100 bp = 1%
    min_minor = models.PositiveIntegerField(default=0)
    max_minor = models.PositiveIntegerField(default=0)         # 0 = no cap
    effective_from = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=255)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pricing_rule"
        indexes = [models.Index(fields=["kind", "product", "status", "effective_from"])]

    def __str__(self) -> str:
        return f"{self.kind}:{self.product} {self.describe()} from {self.effective_from:%Y-%m-%d}"

    def amount_for(self, amount_minor: int) -> int:
        value = self.flat_minor + (amount_minor * self.percent_bp + 5000) // 10000
        value = max(value, self.min_minor)
        if self.max_minor:
            value = min(value, self.max_minor)
        return max(0, min(value, amount_minor))

    def describe(self) -> str:
        parts = []
        if self.flat_minor:
            parts.append(f"GH₵{self.flat_minor / 100:,.2f}")
        if self.percent_bp:
            parts.append(f"{self.percent_bp / 100:g}%")
        text = " + ".join(parts) or "free"
        if self.min_minor:
            text += f", min GH₵{self.min_minor / 100:,.2f}"
        if self.max_minor:
            text += f", max GH₵{self.max_minor / 100:,.2f}"
        return text


class AgentCommission(TimeStampedModel):
    """Commission earned by an agent on one cash-in / cash-out (accrued in the ledger)."""

    agent = models.ForeignKey("agents.Agent", on_delete=models.PROTECT, related_name="commissions")
    txn = models.OneToOneField("agents.AgentTxn", on_delete=models.PROTECT, related_name="commission")
    product = models.CharField(max_length=16)
    amount_minor = models.BigIntegerField()
    rule = models.ForeignKey(PriceRule, on_delete=models.PROTECT, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pricing_agent_commission"
        indexes = [models.Index(fields=["agent", "created_at"]), models.Index(fields=["paid_at"])]



class Promotion(TimeStampedModel):
    """
    A time-limited discount on a fee: a percentage off, or free. For one product, or every
    fee (product = "all"). Proposed by one finance user, approved by another; an approved
    promotion can be ended early (recorded with who and why).
    """

    ALL = "all"

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ENDED = "ended", "Ended early"

    name = models.CharField(max_length=80)                      # shown to customers, e.g. "Christmas: free transfers"
    product = models.CharField(max_length=16)                   # a PriceRule.Product value, or "all"
    percent_off_bp = models.PositiveIntegerField()              # 10 000 = 100% off (free)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=255)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    ended_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "pricing_promotion"
        indexes = [models.Index(fields=["status", "starts_at", "ends_at"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.describe()})"

    def describe(self) -> str:
        what = "every fee" if self.product == self.ALL else PriceRule.Product(self.product).label.split(" (")[0]
        off = "free" if self.percent_off_bp >= 10_000 else f"{self.percent_off_bp / 100:g}% off"
        return f"{off}: {what}"

    def discount_on(self, fee_minor: int) -> int:
        return min(fee_minor, (fee_minor * self.percent_off_bp + 5000) // 10_000)
