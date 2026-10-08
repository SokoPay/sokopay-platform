"""Reconciliation engine (pure) and persistence."""

import datetime as dt

import pytest

from apps.reconciliation import services
from apps.reconciliation.engine import classify
from apps.reconciliation.models import ReconItem


def test_classify_all_buckets():
    ours = {"A": 100, "B": 200, "C": 300}       # A matches, B mismatches, C ours-only
    theirs = {"A": 100, "B": 250, "D": 400}     # D theirs-only
    r = classify(ours, theirs)
    assert r.matched == ["A"]
    assert r.amount_mismatch == [("B", 200, 250)]
    assert r.ours_only == [("C", 300)]
    assert r.theirs_only == [("D", 400)]
    assert not r.is_clean


def test_classify_clean_when_identical():
    r = classify({"A": 1, "B": 2}, {"A": 1, "B": 2})
    assert r.is_clean
    assert r.matched == ["A", "B"]


@pytest.mark.django_db
def test_reconcile_persists_run_and_breaks():
    ours = {"A": 100, "B": 200, "C": 300}
    theirs = {"A": 100, "B": 250, "D": 400}
    run = services.reconcile("mock", dt.date(2026, 10, 7), ours, theirs)

    assert run.matched_count == 1
    assert run.break_count == 3        # 1 mismatch + 1 ours_only + 1 theirs_only
    assert not run.is_clean
    kinds = set(run.items.values_list("kind", flat=True))
    assert kinds == {
        ReconItem.Kind.MISMATCH, ReconItem.Kind.OURS_ONLY, ReconItem.Kind.THEIRS_ONLY,
    }


@pytest.mark.django_db
def test_reconcile_clean_run_has_no_items():
    run = services.reconcile("mock", dt.date(2026, 10, 7), {"A": 1}, {"A": 1})
    assert run.is_clean
    assert run.items.count() == 0
