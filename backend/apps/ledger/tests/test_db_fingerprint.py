"""The restore check must catch a restored copy that differs from the original."""

import json

import pytest
from django.core.management import call_command

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry

pytestmark = pytest.mark.django_db


def test_fingerprint_matches_itself_and_catches_a_difference(tmp_path):
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 10_00),
                        credit(accounts.customer_wallet("11111111-1111-1111-1111-111111111111"), 10_00)])
    before = tmp_path / "before.json"
    call_command("db_fingerprint", out=str(before))
    call_command("db_fingerprint", compare=str(before))          # identical: no exit
    assert json.loads(before.read_text())["ledger_sum"] == 0
    post_entry("one more", [debit(accounts.partner_clearing("mock"), 1),
                            credit(accounts.customer_wallet("11111111-1111-1111-1111-111111111111"), 1)])
    with pytest.raises(SystemExit):
        call_command("db_fingerprint", compare=str(before))
