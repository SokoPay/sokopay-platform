"""
AI usage records. Raw prompts and tool payloads are never stored: only the staff
member's question after redaction, which tools ran, token counts and the outcome.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class AiInteraction(TimeStampedModel):
    class Kind(models.TextChoices):
        CHAT = "chat", "Question"
        BRIEFING = "briefing", "Daily briefing"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    question = models.TextField(blank=True)                 # after redaction
    redactions = models.JSONField(default=list, blank=True)  # kinds removed, e.g. ["phone"]
    tools_used = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, default="ok")   # ok | refused | error
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "insights_ai_interaction"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["created_at"]), models.Index(fields=["user", "created_at"])]


class AiBriefing(TimeStampedModel):
    for_date = models.DateField(db_index=True)
    text = models.TextField()
    model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    class Meta:
        db_table = "insights_ai_briefing"
        ordering = ("-created_at",)
