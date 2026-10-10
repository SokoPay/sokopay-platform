"""
Outbound and inbound redaction for the AI gateway: the last line of defence.

The tools only ever produce aggregates, so in normal operation nothing here fires. It
exists for the cases design can't rule out: a member of staff pasting a customer's phone
number into the chat, a code string that happens to carry an identifier, or the model
echoing something back. Anything that looks like personal or transaction-identifying
data is replaced with a placeholder before it leaves, and again before an answer is shown.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ghana_card", re.compile(r"\bGHA-?\s?\d{9}-?\s?\d\b", re.I)),
    ("reference", re.compile(r"\bSP-[A-Z0-9]{6,}\b", re.I)),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("phone", re.compile(r"(?<!\d)(?:\+?233[\s-]?|0)[2-5]\d(?:[\s-]?\d){7}(?!\d)")),
    ("passport", re.compile(r"\b[A-Z]\d{7,8}\b")),
    ("card_number", re.compile(r"(?<!\d)(?:\d[\s-]?){13,19}(?!\d)")),
    ("long_number", re.compile(r"(?<![\d.,])\d{7,}(?![\d.,])")),   # wallet IDs, account numbers
]


def scrub(text: str) -> tuple[str, list[str]]:
    """Return (text with identifiers replaced, the kinds that were found)."""
    found: list[str] = []
    out = text or ""
    for kind, pattern in _PATTERNS:
        out, n = pattern.subn(f"[{kind} removed]", out)
        if n:
            found.append(kind)
    return out, found


def scrub_obj(obj):
    """Recursively scrub every string inside dicts/lists (numbers pass through)."""
    found: list[str] = []

    def walk(value):
        if isinstance(value, str):
            clean, hits = scrub(value)
            found.extend(hits)
            return clean
        if isinstance(value, dict):
            return {str(walk(k)): walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        return value

    return walk(obj), found


MIN_GROUP = 5


def k_anon(count: int):
    """Small groups could single someone out: report fewer than 5 as "<5"."""
    return "<5" if 0 < int(count) < MIN_GROUP else int(count)
