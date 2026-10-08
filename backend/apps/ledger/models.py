"""
The double-entry ledger — the financial core of SokoPay.

WHY DOUBLE ENTRY
----------------
Every movement of money touches at least two accounts, and the amounts always sum to
zero: money is never created or destroyed, only moved. This makes the books
self-checking. The old SokoPay kept a single editable `wallet_balance` number and had
scripts to "correct" it when it drifted — exactly the failure this design prevents.

THE SIGN CONVENTION (read this once, rely on it everywhere)
-----------------------------------------------------------
* Amounts are integers in the smallest currency unit (pesewas). No floats, no fractions.
* A posting's `amount` is SIGNED: a debit is positive (+), a credit is negative (-).
* Every journal entry's postings sum to exactly 0.
* An account's RAW balance = the sum of its postings' signed amounts.
* Whether a positive raw balance is "good" depends on the account type:
    - ASSET and EXPENSE accounts increase on the debit (+) side  → normal sign +1
    - LIABILITY, INCOME and EQUITY accounts increase on the credit (-) side → normal sign -1
  The "natural" (human-facing) balance = raw balance × normal sign, so a healthy
  merchant payable (a liability we owe them) shows as a positive natural balance.

APPEND-ONLY
-----------
Postings and entries are never updated or deleted. A mistake is fixed by posting a new,
reversing entry. The database role the app uses is granted INSERT (not UPDATE/DELETE) on
these tables in production (see docs/BUILD-STANDARDS.md).
"""

from __future__ import annotations

from django.db import models

from apps.common.models import TimeStampedModel


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"
    EQUITY = "equity", "Equity"


# Normal (increase) side per account type, as a sign multiplier.
NORMAL_SIGN = {
    AccountType.ASSET: +1,
    AccountType.EXPENSE: +1,
    AccountType.LIABILITY: -1,
    AccountType.INCOME: -1,
    AccountType.EQUITY: -1,
}


class Currency(models.Model):
    """An ISO 4217 currency and how many minor units make one major unit."""

    code = models.CharField(max_length=3, primary_key=True)  # e.g. "GHS"
    name = models.CharField(max_length=64)
    minor_units = models.PositiveSmallIntegerField(default=100)  # pesewas per cedi

    class Meta:
        db_table = "ledger_currency"
        verbose_name_plural = "currencies"

    def __str__(self) -> str:
        return self.code


class LedgerAccount(TimeStampedModel):
    """
    One account in the chart of accounts.

    `code` is a stable, human-readable key such as:
        merchant_payable:<merchant_id>     (liability — money we owe a merchant)
        partner_clearing:korba             (asset — money the rail owes us)
        fee_revenue                        (income)
        partner_cost:korba                 (expense)
        suspense                           (liability — unmatched funds)
    See apps/ledger/accounts.py for the catalogue and helpers that create these.
    """

    code = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128)
    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="accounts")

    # Optional link back to what the account belongs to (a merchant, agent, biller…).
    owner_type = models.CharField(max_length=32, blank=True)
    owner_id = models.CharField(max_length=64, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "ledger_account"
        indexes = [models.Index(fields=["owner_type", "owner_id"])]

    def __str__(self) -> str:
        return self.code

    @property
    def normal_sign(self) -> int:
        return NORMAL_SIGN[AccountType(self.account_type)]


class JournalEntry(TimeStampedModel):
    """
    A single balanced transaction in the ledger: a set of postings summing to zero.

    `idempotency_key` makes posting safe to retry: if the same key is seen twice
    (e.g. a duplicated payment-provider callback), we return the first entry instead
    of posting again. This is the defence the old codebase lacked, where duplicate
    callbacks credited balances twice.
    """

    narrative = models.CharField(max_length=255)

    # What this entry relates to, for traceability (e.g. "payment", <payment id>).
    reference_type = models.CharField(max_length=32, blank=True)
    reference_id = models.CharField(max_length=64, blank=True)

    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)

    # The HTTP request that caused this entry (X-Request-ID), linking money movements to
    # API logs and audit events. Blank for scheduled jobs and partner callbacks.
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        db_table = "ledger_journal_entry"
        indexes = [models.Index(fields=["reference_type", "reference_id"])]

    def __str__(self) -> str:
        return f"{self.narrative} ({self.id})"


class Posting(models.Model):
    """
    One leg of a journal entry: a signed amount applied to one account.

    Append-only. There is no updated_at and no edit path by design.
    """

    id = models.BigAutoField(primary_key=True)
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="postings")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="postings")
    # Signed minor units: debit > 0, credit < 0.
    amount = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ledger_posting"
        indexes = [models.Index(fields=["account", "created_at"])]

    def __str__(self) -> str:
        side = "DR" if self.amount >= 0 else "CR"
        return f"{side} {abs(self.amount)} {self.account.code}"


class AccountBalance(models.Model):
    """
    A cached running balance per account, kept in step with postings inside the same
    database transaction and under a row lock. It is an optimisation: the source of
    truth is always SUM(postings.amount). An hourly job re-verifies the two agree.

    `version` increments on every update (optimistic-concurrency breadcrumb + audit).
    """

    account = models.OneToOneField(
        LedgerAccount, on_delete=models.PROTECT, related_name="balance", primary_key=True
    )
    balance = models.BigIntegerField(default=0)  # raw signed balance, minor units
    version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ledger_account_balance"

    def __str__(self) -> str:
        return f"{self.account.code}: {self.balance}"
