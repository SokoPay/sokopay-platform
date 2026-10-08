"""
Agent domain models.

Agents help customers put cash into and take cash out of the SokoPay system. This is
a **DEMI-licence** activity (holding e-money float and running an agent network), so
the services that move money here are licence-gated (see services.py). The models
themselves just hold agent identity, status and a record of each cash transaction; the
money lives in the ledger (agent_float:<id> and customer_wallet:<id>).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Agent(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="agent_profile"
    )
    display_name = models.CharField(max_length=150)
    location = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        db_table = "agents_agent"

    def __str__(self) -> str:
        return f"{self.display_name} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class FloatTopUp(TimeStampedModel):
    """
    An agent bought float (paid SokoPay by bank transfer / MoMo). Maker-checker: one
    staff member records it against the payment reference, a DIFFERENT one approves after
    checking the money arrived — only then is the float credited.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved — float credited"
        REJECTED = "rejected", "Rejected"

    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="float_topups")
    amount_minor = models.BigIntegerField()
    payment_reference = models.CharField(max_length=64)       # bank / MoMo reference of the agent's payment
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "agents_float_topup"
        constraints = [models.UniqueConstraint(fields=["payment_reference"], name="uniq_float_topup_payment_ref")]


class CashOutWindow(TimeStampedModel):
    """
    The customer pressed "Cash out" in their app, standing at an agent. For a few minutes
    ONE agent request may be raised against their account; with no open window, agents
    are refused. The window is used up by the first request (or cancelled / expires).
    """

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="cash_out_windows")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agents_cash_out_window"
        indexes = [models.Index(fields=["customer", "expires_at"])]

    def is_open(self, now) -> bool:
        return self.used_at is None and self.cancelled_at is None and self.expires_at > now


class CashOutRequest(TimeStampedModel):
    """
    An agent asking to pay a customer cash out of their wallet. Nothing moves until the
    customer approves it in their own app with their PIN ("Allow CashOut") — an agent
    can never debit a wallet on their own. Requests expire after a few minutes.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for the customer"
        APPROVED = "approved", "Approved — cash paid out"
        DECLINED = "declined", "Declined by the customer"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Couldn't be completed"

    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="cash_out_requests")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="cash_out_requests")
    amount_minor = models.BigIntegerField()
    fee_minor = models.BigIntegerField(default=0)       # customer pays on top; fixed when requested
    currency = models.CharField(max_length=3, default="GHS")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    txn = models.OneToOneField("AgentTxn", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="cash_out_request")

    class Meta:
        db_table = "agents_cash_out_request"
        indexes = [models.Index(fields=["customer", "status", "expires_at"])]

    def __str__(self) -> str:
        return f"Cash-out {self.amount_minor} {self.status} ({self.agent_id} → {self.customer_id})"


class AgentTxn(TimeStampedModel):
    """A record of an agent cash operation, for history and audit."""

    class Kind(models.TextChoices):
        TOPUP = "topup", "Float top-up"
        CASH_IN = "cash_in", "Cash in"
        CASH_OUT = "cash_out", "Cash out"

    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    customer_phone = models.CharField(max_length=16, blank=True)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="GHS")

    class Meta:
        db_table = "agents_txn"
        indexes = [models.Index(fields=["agent", "created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount_minor} by {self.agent_id}"
