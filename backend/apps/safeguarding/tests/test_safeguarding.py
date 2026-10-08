"""E-money safeguarding: liabilities from the ledger vs trust balances."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.safeguarding import services
from apps.safeguarding.models import SafeguardingCheck, TrustAccount

pytestmark = pytest.mark.django_db


def _issue_e_money(wallet_minor=0, agent_minor=0):
    """Put e-money in a wallet and an agent's float, backed by clearing (as top-ups do)."""
    clearing = accounts.partner_clearing("mock")
    if wallet_minor:
        post_entry("t", [debit(clearing, wallet_minor),
                         credit(accounts.customer_wallet("U1"), wallet_minor)])
    if agent_minor:
        post_entry("t", [debit(clearing, agent_minor),
                         credit(accounts.agent_float("A1"), agent_minor)])


@pytest.fixture
def trust():
    return TrustAccount.objects.create(bank_name="Test Bank", account_name="SokoPay Trust",
                                       account_last4="1234")


def test_liabilities_cover_wallets_and_agent_float():
    _issue_e_money(wallet_minor=700_00, agent_minor=300_00)
    assert services.e_money_liabilities() == 1_000_00


def test_nothing_to_safeguard_is_ok():
    assert services.run_check().status == SafeguardingCheck.Status.OK


def test_e_money_without_any_trust_balance_is_flagged():
    _issue_e_money(wallet_minor=100_00)
    assert services.run_check().status == SafeguardingCheck.Status.NO_DATA


def test_fully_backed(trust):
    _issue_e_money(wallet_minor=1_000_00)
    services.record_trust_balance(account=trust, balance_minor=1_200_00, as_of=timezone.now())
    check = services.run_check()
    assert check.status == SafeguardingCheck.Status.OK
    assert check.surplus_minor == 200_00


def test_shortfall_detected(trust):
    _issue_e_money(wallet_minor=1_000_00)
    services.record_trust_balance(account=trust, balance_minor=900_00, as_of=timezone.now())
    check = services.run_check()
    assert check.status == SafeguardingCheck.Status.SHORTFALL
    assert check.surplus_minor == -100_00


def test_stale_balance_flagged(trust):
    _issue_e_money(wallet_minor=100_00)
    services.record_trust_balance(account=trust, balance_minor=1_000_00,
                                  as_of=timezone.now() - timedelta(days=3))
    assert services.run_check().status == SafeguardingCheck.Status.STALE
