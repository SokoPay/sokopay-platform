"""
Fingerprint the database, to prove a restored backup is identical to the original.

    python manage.py db_fingerprint --out before.json          # on the source
    python manage.py db_fingerprint --compare before.json      # on the restored copy

The fingerprint holds: row counts of every money and identity table, a SHA-256 over every
ledger account's cached balance, the latest journal entry, the whole-ledger sum (must be
zero) and the integrity check (cached balances equal the postings). --compare exits 1 on
any difference, so it can gate a DR drill or a restore runbook.
"""

from __future__ import annotations

import hashlib
import json
import sys

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.models import Sum

TABLES = [
    "accounts.User", "kyc.KycProfile", "ledger.LedgerAccount", "ledger.JournalEntry", "ledger.Posting",
    "ledger.AccountBalance", "payments.Payment", "merchants.Merchant", "merchants.Settlement", "merchants.Refund",
    "merchants.Dispute", "agents.Agent", "agents.AgentTxn", "agents.CashOutRequest", "wallet.ExternalTransfer",
    "wallet.CrossBorderTransfer", "bulk.BulkPayoutItem", "compliance.Alert", "compliance.SuspiciousTransactionReport",
    "safeguarding.SafeguardingCheck", "pricing.PriceRule",
]


def fingerprint() -> dict:
    from apps.ledger.models import AccountBalance, JournalEntry, Posting
    from apps.ledger.tasks import check_integrity
    counts = {label: apps.get_model(label).objects.count() for label in TABLES}
    h = hashlib.sha256()
    for code, bal in AccountBalance.objects.select_related("account").order_by("account__code") \
            .values_list("account__code", "balance"):
        h.update(f"{code}={bal};".encode())
    last = JournalEntry.objects.order_by("-created_at", "-id").values_list("id", flat=True).first()
    return {
        "counts": counts,
        "balances_sha256": h.hexdigest(),
        "last_entry": str(last) if last else None,
        "ledger_sum": int(Posting.objects.aggregate(s=Sum("amount"))["s"] or 0),
        "integrity_breaks": len(check_integrity()),
    }


class Command(BaseCommand):
    help = "Fingerprint the database (row counts, balances checksum, ledger integrity)."

    def add_arguments(self, parser):
        parser.add_argument("--out", help="write the fingerprint to this JSON file")
        parser.add_argument("--compare", help="compare against this fingerprint file; exit 1 on any difference")

    def handle(self, *args, out=None, compare=None, **opts):
        fp = fingerprint()
        problems = []
        if fp["ledger_sum"] != 0:
            problems.append(f"ledger does not sum to zero ({fp['ledger_sum']})")
        if fp["integrity_breaks"]:
            problems.append(f"{fp['integrity_breaks']} account(s) where the cached balance != postings")
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(fp, f, indent=2, sort_keys=True)
        if compare:
            with open(compare, encoding="utf-8") as f:
                want = json.load(f)
            for key in ("balances_sha256", "last_entry", "ledger_sum"):
                if want.get(key) != fp.get(key):
                    problems.append(f"{key}: expected {want.get(key)}, got {fp.get(key)}")
            for table, n in want.get("counts", {}).items():
                if fp["counts"].get(table) != n:
                    problems.append(f"{table}: expected {n} rows, got {fp['counts'].get(table)}")
        self.stdout.write(json.dumps({"fingerprint": fp, "problems": problems}, indent=2, sort_keys=True))
        if problems:
            sys.exit(1)
