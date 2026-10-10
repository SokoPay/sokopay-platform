"""Scheduled AI briefing (runs only when the AI provider is switched on)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("sokopay.ai")


@shared_task
def ai_daily_briefing() -> str:
    from .ai import gateway
    if not gateway.provider_enabled():
        return "off"
    try:
        gateway.daily_briefing()
    except gateway.AiUnavailable as exc:
        logger.warning("Daily AI briefing skipped: %s", exc)
        return "skipped"
    return "ok"
