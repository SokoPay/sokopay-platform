"""
Fraud & AML compliance records.

  Alert            a rule or screening hit on a customer, agent or merchant, worked by
                   the compliance team (notes, holds, decisions) — never shown to the
                   customer (no tipping-off).
  AlertNote        the alert's audit trail: every note, status change and hold.
  LargeTransactionReport  movements at/above the reporting threshold, for the FIC.
  SuspiciousTransactionReport  an STR drafted from alerts, approved by a SECOND officer,
                   exported and filed with the Financial Intelligence Centre (FIC).
  WatchlistEntry / WatchlistImport  sanctions & PEP lists loaded from files.
  ScreeningMatch   a customer whose name resembles a watchlist entry, pending review.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Severity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class Alert(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        ESCALATED = "escalated", "Escalated"
        CLOSED_NO_ACTION = "closed_no_action", "Closed — no further action"
        CLOSED_REPORTED = "closed_reported", "Closed — reported to FIC"

    class SubjectKind(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        AGENT = "agent", "Agent"
        MERCHANT = "merchant", "Merchant"

    rule = models.CharField(max_length=40, db_index=True)
    title = models.CharField(max_length=160)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    subject_kind = models.CharField(max_length=10, choices=SubjectKind.choices)
    subject_id = models.CharField(max_length=64)          # user / agent / merchant id
    subject_label = models.CharField(max_length=160)      # name + masked phone, for the queue
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                             blank=True, related_name="aml_alerts")  # the person behind it

    hits = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    evidence = models.JSONField(default=list, blank=True)  # latest triggering facts (capped)

    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="aml_alerts_assigned")
    closed_reason = models.CharField(max_length=255, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "compliance_alert"
        indexes = [
            models.Index(fields=["status", "severity", "created_at"]),
            models.Index(fields=["subject_kind", "subject_id", "rule", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.rule} on {self.subject_label} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.OPEN, self.Status.INVESTIGATING, self.Status.ESCALATED)


class AlertNote(models.Model):
    class Kind(models.TextChoices):
        NOTE = "note", "Note"
        STATUS = "status", "Status change"
        HOLD = "hold", "Hold placed"
        RELEASE = "release", "Hold released"
        ASSIGN = "assign", "Assigned"
        SYSTEM = "system", "System"

    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.NOTE)
    text = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "compliance_alert_note"
        ordering = ("created_at",)


class LargeTransactionReport(models.Model):
    """A wallet movement at/above the large-transaction threshold (regulatory record)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entry_id = models.CharField(max_length=80, unique=True)   # "<entry uuid>:<user uuid>"
    kind = models.CharField(max_length=32)          # ledger reference type: cash_in, p2p…
    direction = models.CharField(max_length=3)      # in | out
    amount_minor = models.BigIntegerField()
    occurred_at = models.DateTimeField()
    exported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "compliance_large_txn_report"
        indexes = [models.Index(fields=["exported_at", "occurred_at"])]


class SuspiciousTransactionReport(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved — ready to file"
        FILED = "filed", "Filed with FIC"

    alerts = models.ManyToManyField(Alert, related_name="reports")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                             blank=True, related_name="+")
    subject_label = models.CharField(max_length=160)
    narrative = models.TextField(max_length=10000)
    transactions = models.JSONField(default=list, blank=True)   # snapshot at drafting time
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    filed_at = models.DateTimeField(null=True, blank=True)
    fic_reference = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "compliance_str"


class WatchlistEntry(models.Model):
    class Source(models.TextChoices):
        UN = "un", "UN Security Council consolidated list"
        OFAC = "ofac", "US OFAC SDN"
        EU = "eu", "EU consolidated list"
        GH = "gh", "Ghana domestic list"
        PEP = "pep", "Politically exposed persons"
        CUSTOM = "custom", "Internal / custom"

    class Kind(models.TextChoices):
        SANCTIONS = "sanctions", "Sanctions"
        PEP = "pep", "Politically exposed person"

    source = models.CharField(max_length=8, choices=Source.choices, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=255)
    aliases = models.JSONField(default=list, blank=True)
    normalised = models.JSONField(default=list, blank=True)    # normalised name + aliases, for matching
    dob = models.CharField(max_length=32, blank=True)
    nationality = models.CharField(max_length=64, blank=True)
    reference = models.CharField(max_length=64, blank=True)     # the list's own id
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "compliance_watchlist_entry"

    def __str__(self) -> str:
        return f"{self.name} ({self.source})"


class WatchlistImport(models.Model):
    source = models.CharField(max_length=8, choices=WatchlistEntry.Source.choices)
    filename = models.CharField(max_length=255)
    entries = models.PositiveIntegerField()
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "compliance_watchlist_import"


class ScreeningMatch(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        CONFIRMED = "confirmed", "Confirmed match"
        CLEARED = "cleared", "Cleared — not the same person"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="screening_matches")
    entry = models.ForeignKey(WatchlistEntry, on_delete=models.SET_NULL, null=True, related_name="matches")
    entry_name = models.CharField(max_length=255)      # kept if the list entry is later removed
    entry_kind = models.CharField(max_length=10)
    name_screened = models.CharField(max_length=255)
    score = models.DecimalField(max_digits=4, decimal_places=3)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    alert = models.ForeignKey(Alert, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        db_table = "compliance_screening_match"
        constraints = [models.UniqueConstraint(fields=["user", "entry"], name="uniq_user_watchlist_entry")]
