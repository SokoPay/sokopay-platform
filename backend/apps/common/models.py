"""Shared abstract base models used across the platform."""

import uuid

from django.db import models


class TimeStampedModel(models.Model):
    """
    Abstract base: a UUID primary key plus created/updated timestamps.

    We use UUID primary keys (not sequential integers) for anything exposed in a
    URL or API so that identifiers can't be guessed or enumerated — a weakness in
    the old codebase, where endpoints took /<int:pk>/ and leaked existence.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("-created_at",)


class AuditEvent(models.Model):
    """
    Append-only record of a privileged or sensitive action: who did what, to what, when,
    from which request. Written by apps.common.audit.record(); never updated or deleted
    (enforced here and, on PostgreSQL, by a database trigger — see migration 0001).
    """

    id = models.BigAutoField(primary_key=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    actor_label = models.CharField(max_length=160, blank=True)    # kept even if the user is later anonymised
    action = models.CharField(max_length=64, db_index=True)       # e.g. "kyc.hold", "pricing.approve"
    object_type = models.CharField(max_length=48, blank=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    summary = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    ip = models.CharField(max_length=45, blank=True)

    class Meta:
        db_table = "common_audit_event"
        ordering = ("-at",)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit events are append-only.")
