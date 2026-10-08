"""
Post-commit hooks on ledger entries.

Every movement of money goes through `post_entry`, which makes it the one place to
observe all of it. Other apps (e.g. compliance transaction monitoring) register a
callback here from their AppConfig.ready(); it receives the JournalEntry id AFTER the
surrounding transaction commits — never for money that was rolled back, and never
inside the money-moving transaction (a slow or failing hook can't block a payment).

The ledger depends on nothing; observers depend on the ledger.
"""

from __future__ import annotations

import logging
from typing import Callable

from django.db import transaction

logger = logging.getLogger("sokopay.ledger")

_hooks: list[Callable[[int | str], None]] = []


def register(hook: Callable[[int | str], None]) -> None:
    if hook not in _hooks:
        _hooks.append(hook)


def schedule(entry_id) -> None:
    """Called by post_entry for each NEW entry (not idempotency replays)."""
    for hook in _hooks:
        def _run(h=hook):
            try:
                h(entry_id)
            except Exception:  # an observer must never break money movement
                logger.exception("Ledger post-commit hook %s failed for entry %s", h, entry_id)
        transaction.on_commit(_run)
