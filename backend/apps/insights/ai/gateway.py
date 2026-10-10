"""
The one door between SokoPay and an external AI (Anthropic's Claude). Off by default.

Controls, in order:
  1. Switched off unless AI_PROVIDER=anthropic and ANTHROPIC_API_KEY are set; AI_CHAT_ENABLED
     separately gates the chat (the daily briefing can run without it).
  2. Only staff in AI_ALLOWED_ROLES (default compliance, finance, operations) or superusers.
  3. Per-person rate limit (AI_CHAT_PER_HOUR) and a platform-wide daily token budget
     (AI_DAILY_TOKEN_BUDGET); over either → refused, nothing sent.
  4. The model gets NO database access: it can only call the aggregate tools in tools.py.
  5. Everything sent is passed through redact.scrub_obj (the staff question, tool results);
     everything returned is scrubbed again before it's shown or stored.
  6. Every call is logged in AiInteraction (who, redacted question, tools used, tokens,
     outcome) and in the audit log. Raw prompts and tool payloads are not stored.
  7. Bounded: at most AI_MAX_TOOL_ROUNDS tool rounds, AI_MAX_TOKENS per answer, 60 s timeout.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from apps.common.audit import record as audit

from . import tools
from .redact import scrub, scrub_obj

logger = logging.getLogger("sokopay.ai")

SYSTEM_PROMPT = (
    "You are SokoPay's analytics assistant for back-office staff of a Ghanaian payments company "
    "(Bank of Ghana regulated). Answer only from the tool results you are given; if the data doesn't "
    "answer the question, say so plainly. The tools return aggregates only: you have no access to, and "
    "must never ask for or guess, any individual's identity, phone number, ID number, account or "
    "transaction reference. Counts shown as \"<5\" are deliberately hidden small groups. Amounts are "
    "Ghana cedis (GH₵). Be concise and practical: lead with the answer, then the numbers behind it, then "
    "what staff could check next in the SokoPay portal. Don't give legal or regulatory rulings."
)


class AiUnavailable(Exception):
    """The AI can't be used right now (message is safe to show to staff)."""


def provider_enabled() -> bool:
    return (getattr(settings, "AI_PROVIDER", "off") == "anthropic"
            and bool(getattr(settings, "ANTHROPIC_API_KEY", "")))


def chat_enabled() -> bool:
    return provider_enabled() and getattr(settings, "AI_CHAT_ENABLED", False)


def user_allowed(user) -> bool:
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "") != "staff":
        return False
    if user.is_superuser:
        return True
    roles = set(getattr(settings, "AI_ALLOWED_ROLES", ("compliance", "finance", "operations")))
    return user.groups.filter(name__in=roles).exists()


def tokens_used_today() -> int:
    from ..models import AiInteraction
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    agg = AiInteraction.objects.filter(created_at__gte=start).aggregate(i=Sum("input_tokens"), o=Sum("output_tokens"))
    return int(agg["i"] or 0) + int(agg["o"] or 0)


def _check_budget():
    if tokens_used_today() >= int(getattr(settings, "AI_DAILY_TOKEN_BUDGET", 300_000)):
        raise AiUnavailable("Today's AI budget has been used up. It resets at midnight.")


def _check_rate(user):
    key = f"ai:rate:{user.pk}"
    limit = int(getattr(settings, "AI_CHAT_PER_HOUR", 20))
    if cache.add(key, 1, 3600):
        return
    try:
        n = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 3600)
        n = 1
    if n > limit:
        raise AiUnavailable(f"You've asked {limit} questions in the last hour. Please wait a little.")


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60, max_retries=2)


def _text(blocks) -> str:
    return "\n".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text").strip()


def _log(*, user, kind, question="", redactions=None, tools_used=None, usage=(0, 0), status="ok", error=""):
    from ..models import AiInteraction
    AiInteraction.objects.create(
        user=user, kind=kind, question=question[:2000], redactions=sorted(set(redactions or [])),
        tools_used=tools_used or [], input_tokens=usage[0], output_tokens=usage[1], status=status, error=error[:255],
        model=getattr(settings, "AI_MODEL", ""))
    audit(f"ai.{kind}", actor=user, status=status, tools=",".join(tools_used or []),
          tokens=usage[0] + usage[1], redactions=",".join(sorted(set(redactions or []))))


def ask(user, question: str) -> dict:
    """Answer a staff question from aggregate tools. Returns {"answer", "redactions", "tools"}."""
    if not chat_enabled():
        raise AiUnavailable("The AI assistant is switched off.")
    if not user_allowed(user):
        raise AiUnavailable("Your staff role doesn't include the AI assistant.")
    question = (question or "").strip()[:1000]
    if len(question) < 3:
        raise AiUnavailable("Ask a question about the platform's figures.")
    _check_rate(user)
    _check_budget()
    clean_q, redactions = scrub(question)
    messages = [{"role": "user", "content": clean_q}]
    used, usage = [], [0, 0]
    rounds = int(getattr(settings, "AI_MAX_TOOL_ROUNDS", 5))
    try:
        client = _client()
        for _ in range(rounds + 1):
            resp = client.messages.create(
                model=settings.AI_MODEL, max_tokens=int(getattr(settings, "AI_MAX_TOKENS", 1500)),
                system=SYSTEM_PROMPT, tools=tools.tool_specs(), messages=messages)
            usage[0] += getattr(resp.usage, "input_tokens", 0) or 0
            usage[1] += getattr(resp.usage, "output_tokens", 0) or 0
            calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                answer, out_hits = scrub(_text(resp.content) or "I couldn't produce an answer.")
                redactions += out_hits
                _log(user=user, kind="chat", question=clean_q, redactions=redactions, tools_used=used,
                     usage=tuple(usage))
                return {"answer": answer, "redactions": sorted(set(redactions)), "tools": used}
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input or {}} if b.type == "tool_use"
                else {"type": "text", "text": getattr(b, "text", "")}
                for b in resp.content if getattr(b, "type", "") in ("text", "tool_use")]})
            results = []
            for call in calls:
                used.append(call.name)
                payload, hits = scrub_obj(tools.run(call.name, call.input or {}))
                redactions += hits
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": json.dumps(payload, default=str)})
            messages.append({"role": "user", "content": results})
        raise AiUnavailable("The question needed too many steps. Try something more specific.")
    except AiUnavailable as exc:
        _log(user=user, kind="chat", question=clean_q, redactions=redactions, tools_used=used,
             usage=tuple(usage), status="refused", error=str(exc))
        raise
    except Exception as exc:  # network, auth, API errors: never leak details to the page
        logger.warning("AI chat failed: %s", type(exc).__name__)
        _log(user=user, kind="chat", question=clean_q, redactions=redactions, tools_used=used,
             usage=tuple(usage), status="error", error=type(exc).__name__)
        raise AiUnavailable("The AI service didn't respond. Please try again later.") from None


BRIEFING_TOOLS = ("platform_overview", "daily_volumes", "failure_breakdown", "aml_summary",
                  "compliance_status", "settlement_summary", "statistical_insights")


def daily_briefing(*, user=None):
    """Write the morning briefing from aggregate tool outputs (no tool calls by the model)."""
    from ..models import AiBriefing
    if not provider_enabled():
        raise AiUnavailable("The AI service is switched off.")
    _check_budget()
    data, redactions = scrub_obj({name: tools.run(name, {"days": 7} if name != "daily_volumes" else {"days": 14})
                                  for name in BRIEFING_TOOLS})
    prompt = ("Write today's operations briefing for SokoPay's management from this data. Sections: "
              "Headline (2 sentences), Volumes, Failures, Compliance & risk, Settlements, Watch today "
              "(up to 5 bullets). Use only this data.\n\n" + json.dumps(data, default=str))
    try:
        resp = _client().messages.create(model=settings.AI_MODEL, max_tokens=int(getattr(settings, "AI_MAX_TOKENS", 1500)),
                                         system=SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        logger.warning("AI briefing failed: %s", type(exc).__name__)
        _log(user=user, kind="briefing", redactions=redactions, tools_used=list(BRIEFING_TOOLS),
             status="error", error=type(exc).__name__)
        raise AiUnavailable("The AI service didn't respond. Please try again later.") from None
    text, hits = scrub(_text(resp.content))
    usage = (getattr(resp.usage, "input_tokens", 0) or 0, getattr(resp.usage, "output_tokens", 0) or 0)
    _log(user=user, kind="briefing", redactions=redactions + hits, tools_used=list(BRIEFING_TOOLS), usage=usage)
    return AiBriefing.objects.create(for_date=timezone.localdate(), text=text, model=settings.AI_MODEL,
                                     input_tokens=usage[0], output_tokens=usage[1], created_by=user)
