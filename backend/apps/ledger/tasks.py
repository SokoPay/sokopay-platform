"""
Ledger integrity check.

The cached per-account balance is an optimisation; the source of truth is the sum of
the postings. This job re-derives every balance and flags any account where the cache
has drifted — a tripwire for bugs or tampering. In a healthy system it always reports
zero breaks.
"""

from __future__ import annotations

import logging

from celery import shared_task

from .models import LedgerAccount
from .services import balance_of, recompute_balance

logger = logging.getLogger("sokopay.ledger")


def check_integrity() -> list[dict]:
    """Return a list of drifted accounts (empty when the ledger is consistent)."""
    breaks = []
    for account in LedgerAccount.objects.all().iterator():
        cached = balance_of(account)
        actual = recompute_balance(account)
        if cached != actual:
            breaks.append({"account": account.code, "cached": cached, "actual": actual})
    if breaks:
        logger.error("Ledger integrity breaks detected: %s", breaks)
    return breaks


@shared_task(name="apps.ledger.tasks.verify_ledger_integrity")
def verify_ledger_integrity() -> dict:
    breaks = check_integrity()
    return {"breaks": len(breaks), "accounts": [b["account"] for b in breaks]}
