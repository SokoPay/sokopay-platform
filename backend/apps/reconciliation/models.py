"""Persisted reconciliation runs and the breaks found, for ops to investigate."""

from __future__ import annotations

from django.db import models

from apps.common.models import TimeStampedModel


class ReconRun(TimeStampedModel):
    rail = models.CharField(max_length=16)
    date = models.DateField()
    matched_count = models.IntegerField(default=0)
    mismatch_count = models.IntegerField(default=0)
    ours_only_count = models.IntegerField(default=0)
    theirs_only_count = models.IntegerField(default=0)

    class Meta:
        db_table = "recon_run"
        indexes = [models.Index(fields=["rail", "date"])]

    def __str__(self) -> str:
        return f"Recon {self.rail} {self.date} (breaks={self.break_count})"

    @property
    def break_count(self) -> int:
        return self.mismatch_count + self.ours_only_count + self.theirs_only_count

    @property
    def is_clean(self) -> bool:
        return self.break_count == 0


class ReconItem(TimeStampedModel):
    class Kind(models.TextChoices):
        MISMATCH = "mismatch", "Amount mismatch"
        OURS_ONLY = "ours_only", "In our records only"
        THEIRS_ONLY = "theirs_only", "In the rail's records only"

    run = models.ForeignKey(ReconRun, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    rail_ref = models.CharField(max_length=64)
    ours_minor = models.BigIntegerField(null=True, blank=True)
    theirs_minor = models.BigIntegerField(null=True, blank=True)
    resolved = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    resolved_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "recon_item"
        indexes = [models.Index(fields=["run", "resolved"])]
