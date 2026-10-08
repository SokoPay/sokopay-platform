"""Ledger integrity check task."""

import pytest

from apps.ledger import accounts
from apps.ledger.models import AccountBalance
from apps.ledger.services import credit, debit, post_entry
from apps.ledger.tasks import check_integrity

pytestmark = pytest.mark.django_db


def test_integrity_clean_after_normal_postings():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")
    post_entry("c", [debit(clearing, 1000), credit(payable, 1000)])
    assert check_integrity() == []


def test_integrity_detects_drift():
    clearing = accounts.partner_clearing("mock")
    payable = accounts.merchant_payable("M1")
    post_entry("c", [debit(clearing, 1000), credit(payable, 1000)])
    # Corrupt the cached balance to simulate drift/tampering.
    AccountBalance.objects.filter(account=clearing).update(balance=999999)
    breaks = check_integrity()
    assert any(b["account"] == clearing.code for b in breaks)
