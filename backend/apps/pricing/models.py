"""
Configurable prices: what SokoPay charges (fees) and what it pays agents (commissions).

A PriceRule is never edited once approved. To change a price, propose a new rule with a
later `effective_from`; a second staff member (finance) approves it. The rule in force
for a product at a moment is the most recent APPROVED rule whose effective_from has
passed. With no approved rule, the code defaults in services.DEFAULTS apply.

amount = flat_minor + amount × percent_bp / 10 000 (half-up), then clamped to
[min_minor, max_minor] (max 0 = no cap) and never more than the transaction amount.
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
    product = models.CharField(max_length=12, choices=Product.choices)
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
    product = models.CharField(max_length=12)
    amount_minor = models.BigIntegerField()
    rule = models.ForeignKey(PriceRule, on_delete=models.PROTECT, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pricing_agent_commission"
        indexes = [models.Index(fields=["agent", "created_at"]), models.Index(fields=["paid_at"])]
