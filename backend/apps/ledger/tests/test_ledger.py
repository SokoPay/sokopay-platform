"""
Ledger tests — the most important tests in the codebase.

They prove the invariant that makes the books trustworthy:
    across the whole ledger, the sum of all postings is always exactly zero,
    and the cached balances never disagree with the postings.

Single-threaded correctness runs on any database. The concurrency/locking
guarantees (double-spend prevention) require PostgreSQL's SELECT ... FOR UPDATE and
are exercised in the integration suite against Postgres (see docs/BUILD-STANDARDS.md).
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.ledger import accounts
from apps.ledger.exceptions import CurrencyMismatch, InsufficientFunds, UnbalancedEntry
from apps.ledger.models import Currency, Posting
from apps.ledger.services import (
    balance_of,
    credit,
    debit,
    natural_balance_of,
    post_entry,
    recompute_balance,
)

pytestmark = pytest.mark.django_db


def _usd():
    return Currency.objects.get_or_create(
        code="USD", defaults={"name": "US Dollar", "minor_units": 100}
    )[0]


def test_balanced_entry_moves_money_and_updates_balances():
    clearing = accounts.partner_clearing("mock")      # asset
    payable = accounts.merchant_payable("M1")         # liability
    fees = accounts.fee_revenue()                     # income

    # Customer paid GH₵100.00; merchant fee GH₵1.50.
    post_entry(
        "MoMo collection for order 1",
        [debit(clearing, 10_000), credit(payable, 9_850), credit(fees, 150)],
        reference=("payment", "order-1"),
    )

    assert balance_of(clearing) == 10_000          # asset: positive raw
    assert natural_balance_of(payable) == 9_850    # liability: we owe the merchant
    assert natural_balance_of(fees) == 150

    # The whole-ledger invariant: every posting sums to zero.
    assert sum(p.amount for p in Posting.objects.all()) == 0


def test_unbalanced_entry_is_rejected():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")
    with pytest.raises(UnbalancedEntry):
        post_entry("bad", [debit(clearing, 10_000), credit(payable, 9_000)])


def test_currency_mismatch_is_rejected():
    ghs_clearing = accounts.partner_clearing("mock")
    usd_fees = accounts.fee_revenue(currency="USD")
    _usd()
    with pytest.raises(CurrencyMismatch):
        post_entry("mixed", [debit(ghs_clearing, 100), credit(usd_fees, 100)])


def test_idempotency_key_prevents_double_posting():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")

    key = "callback-abc-123"
    e1 = post_entry("collection", [debit(clearing, 5_000), credit(payable, 5_000)],
                    idempotency_key=key)
    # A duplicated payment-provider callback replays the same key.
    e2 = post_entry("collection", [debit(clearing, 5_000), credit(payable, 5_000)],
                    idempotency_key=key)

    assert e1.id == e2.id                     # same entry returned
    assert Posting.objects.count() == 2       # posted only once
    assert balance_of(clearing) == 5_000      # credited once, not twice


def test_protected_liability_cannot_go_negative():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")
    # Give the merchant GH₵50.00.
    post_entry("fund", [debit(clearing, 5_000), credit(payable, 5_000)])
    # Try to settle GH₵80.00 — more than they are owed. Must be refused.
    with pytest.raises(InsufficientFunds):
        post_entry("over-settle", [debit(payable, 8_000), credit(clearing, 8_000)])


def test_allow_negative_lets_clearing_float_go_negative():
    clearing = accounts.partner_prefund("mock")   # asset float we prefunded
    biller = accounts.biller_payable("ECG")
    # Paying a bill before topping up the float drives prefund negative — allowed
    # explicitly for float accounts.
    post_entry(
        "bill pay from float",
        [credit(clearing, 3_000), debit(biller, 3_000)],
        allow_negative={clearing.code, biller.code},
    )
    assert balance_of(clearing) == -3_000


def test_cached_balance_matches_recomputed():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")
    for i in range(10):
        post_entry(f"c{i}", [debit(clearing, 100), credit(payable, 100)])
    assert balance_of(clearing) == recompute_balance(clearing)
    assert balance_of(payable) == recompute_balance(payable)


def test_collection_then_settlement_nets_to_zero_for_merchant():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")
    fees = accounts.fee_revenue()
    cost = accounts.partner_cost("mock")

    # Collect GH₵100, fee GH₵1.50, partner cost GH₵0.70.
    post_entry("collect", [debit(clearing, 10_000), credit(payable, 9_850), credit(fees, 150)])
    post_entry("partner cost", [debit(cost, 70), credit(clearing, 70)],
               allow_negative={clearing.code})
    # Settle the merchant's GH₵98.50.
    post_entry("settle", [debit(payable, 9_850), credit(clearing, 9_850)])

    assert natural_balance_of(payable) == 0          # merchant fully paid out
    assert natural_balance_of(fees) == 150           # we kept the fee
    # Clearing now holds fee minus partner cost (what's left with the rail).
    assert balance_of(clearing) == 10_000 - 70 - 9_850


# --- property-based invariant ----------------------------------------------
# The DB fixture is function-scoped and shared across Hypothesis examples; data
# accumulates within one test run, which is fine here because the invariants we
# assert (whole-ledger sum == 0, cached == recomputed) hold regardless.
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(amounts=st.lists(st.integers(min_value=1, max_value=1_000_000), min_size=1, max_size=30))
def test_whole_ledger_always_sums_to_zero(amounts):
    """
    For any sequence of balanced entries, the sum of ALL postings stays exactly 0
    and each account's cached balance equals its recomputed balance.
    """
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("PROP")
    for i, amt in enumerate(amounts):
        post_entry(f"p{i}", [debit(clearing, amt), credit(payable, amt)])

    assert sum(p.amount for p in Posting.objects.all()) == 0
    assert balance_of(clearing) == recompute_balance(clearing)
    assert balance_of(payable) == recompute_balance(payable)
