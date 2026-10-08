"""
The ONLY sanctioned way to write to the ledger.

Nothing anywhere else in the codebase may create Postings or edit balances directly.
Money moves through `post_entry(...)`. This single, well-tested chokepoint is what
makes the books trustworthy and auditable.

Concurrency safety
------------------
`post_entry` runs inside one database transaction and locks the affected
AccountBalance rows with SELECT ... FOR UPDATE, always in a deterministic order
(by account id) to avoid deadlocks. This prevents the lost-update and double-spend
races that the old codebase was open to (it did read-modify-write on balances with
no locking).

Idempotency
-----------
Pass an `idempotency_key` for anything that could be retried (payment callbacks,
client POSTs). If an entry with that key already exists, the existing entry is
returned and nothing new is posted.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Sum

from . import hooks
from .exceptions import CurrencyMismatch, InsufficientFunds, InvalidEntry, UnbalancedEntry
from apps.common.audit import current_request_id

from .models import AccountBalance, JournalEntry, LedgerAccount, Posting


@dataclass(frozen=True)
class Line:
    """One leg to post: a signed amount (debit > 0, credit < 0) against an account."""

    account: LedgerAccount
    amount: int  # signed minor units


def debit(account: LedgerAccount, amount: int) -> Line:
    """A debit line (positive). `amount` must be a positive integer of minor units."""
    _require_positive(amount)
    return Line(account=account, amount=+amount)


def credit(account: LedgerAccount, amount: int) -> Line:
    """A credit line (negative). `amount` must be a positive integer of minor units."""
    _require_positive(amount)
    return Line(account=account, amount=-amount)


def _require_positive(amount: int) -> None:
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise InvalidEntry("Amount must be a plain integer number of minor units.")
    if amount <= 0:
        raise InvalidEntry("debit()/credit() take a positive amount; the sign is implied.")


@transaction.atomic
def post_entry(
    narrative: str,
    lines: list[Line],
    *,
    idempotency_key: str | None = None,
    reference: tuple[str, str] | None = None,
    allow_negative: set[str] | None = None,
) -> JournalEntry:
    """
    Post a balanced journal entry atomically.

    Parameters
    ----------
    narrative : short human description, e.g. "MoMo collection for order 1234".
    lines : the postings, built with debit()/credit(). Must be ≥ 2 and sum to 0.
    idempotency_key : optional; makes the post safe to retry (see module docstring).
    reference : optional (type, id) pair linking the entry to a domain object.
    allow_negative : account codes permitted to go negative (e.g. clearing/float
        accounts). By default, LIABILITY accounts we owe out (like merchant payables)
        are protected from going negative, which would mean we "owe" a negative amount.

    Returns the created (or, on idempotency hit, the existing) JournalEntry.
    """
    # 1. Idempotency: if we've already posted this key, return that entry untouched.
    if idempotency_key:
        existing = JournalEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # 2. Structural validation.
    if len(lines) < 2:
        raise InvalidEntry("A journal entry needs at least two postings.")
    if sum(line.amount for line in lines) != 0:
        raise UnbalancedEntry(
            "Postings do not sum to zero: "
            + ", ".join(f"{ln.account.code}:{ln.amount}" for ln in lines)
        )

    currencies = {line.account.currency_id for line in lines}
    if len(currencies) != 1:
        raise CurrencyMismatch(
            "All accounts in one entry must share a currency: " + ", ".join(sorted(currencies))
        )

    # 3. Lock the balance rows in a stable order (by account id) to avoid deadlocks.
    accounts_by_id = {line.account.id: line.account for line in lines}
    account_ids = sorted(accounts_by_id)
    # Make sure every balance row exists BEFORE taking any lock (first use of an account
    # creates it; ON CONFLICT DO NOTHING if another transaction just did). Creating rows
    # while already holding locks let two transactions take the same rows in opposite
    # order and deadlock (found by the Postgres concurrency tests).
    existing = set(AccountBalance.objects.filter(account_id__in=account_ids).values_list("account_id", flat=True))
    missing = [aid for aid in account_ids if aid not in existing]
    if missing:
        AccountBalance.objects.bulk_create([AccountBalance(account_id=aid) for aid in missing],
                                           ignore_conflicts=True)
    # One lock statement, rows in a fixed order (by account id).
    locked = {
        bal.account_id: bal
        for bal in AccountBalance.objects.select_for_update().filter(account_id__in=account_ids)
        .order_by("account_id")
    }

    # 4. Create the entry and its postings.
    ref_type, ref_id = reference or ("", "")
    entry = JournalEntry.objects.create(
        narrative=narrative,
        reference_type=ref_type,
        reference_id=ref_id,
        idempotency_key=idempotency_key,
        request_id=current_request_id(),
    )
    Posting.objects.bulk_create(
        [Posting(entry=entry, account=line.account, amount=line.amount) for line in lines]
    )

    # 5. Apply to the cached balances under the lock, enforcing negative-balance rules.
    allow_negative = allow_negative or set()
    # Aggregate the net change per account (an entry may touch one account twice).
    net: dict = {}
    for line in lines:
        net[line.account.id] = net.get(line.account.id, 0) + line.amount

    for account_id, delta in net.items():
        bal = locked[account_id]
        account = accounts_by_id[account_id]
        new_balance = bal.balance + delta
        # A protected account may not cross into a "we owe a negative amount" position.
        if new_balance * account.normal_sign < 0 and account.code not in allow_negative:
            raise InsufficientFunds(
                f"Account {account.code} would go to natural balance "
                f"{new_balance * account.normal_sign}, which is not allowed."
            )
        bal.balance = new_balance
        bal.version = bal.version + 1
        bal.save(update_fields=["balance", "version", "updated_at"])

    # Observers (e.g. AML transaction monitoring) run after the commit — see hooks.py.
    hooks.schedule(entry.id)
    return entry


# --- read helpers -----------------------------------------------------------
def balance_of(account: LedgerAccount) -> int:
    """Cached raw balance (signed minor units). Fast path for most reads."""
    bal = AccountBalance.objects.filter(account=account).first()
    return bal.balance if bal else 0


def natural_balance_of(account: LedgerAccount) -> int:
    """Human-facing balance = raw × normal sign (see models.py sign convention)."""
    return balance_of(account) * account.normal_sign


def recompute_balance(account: LedgerAccount) -> int:
    """
    Authoritative balance straight from the postings (SUM). Used by the hourly
    integrity job to verify the cached balance has not drifted.
    """
    total = account.postings.aggregate(total=Sum("amount"))["total"] or 0
    return int(total)
