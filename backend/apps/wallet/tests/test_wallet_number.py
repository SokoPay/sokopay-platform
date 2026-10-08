"""SokoPay wallet ID: format, check digit, resolution, and use for P2P and agent cash-in."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.agents import services as agents
from apps.agents.exceptions import AgentError
from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.gate import _enabled_set
from apps.wallet import services as wallet
from apps.wallet.accounts_lookup import (
    AccountNotFound,
    is_valid_wallet_number,
    luhn_check_digit,
    resolve_account,
    wallet_number_for,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _demi(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def ama(db):
    return User.objects.create_user(phone="+233244058519", full_name="Ama Mensah")


def test_number_format_and_check_digit(ama):
    n = wallet_number_for(ama)
    assert len(n) == 10 and n.startswith("7") and is_valid_wallet_number(n)
    assert wallet_number_for(ama) == n                                  # stable
    assert luhn_check_digit("7992739871") == "3"                        # textbook Luhn value
    # Every single-digit typo is caught by the check digit.
    for pos in range(1, 10):
        for d in "0123456789":
            if d != n[pos]:
                assert not is_valid_wallet_number(n[:pos] + d + n[pos + 1:])


def test_resolve_by_phone_formats_and_wallet_id(ama):
    n = wallet_number_for(ama)
    for ident in ("0244058519", "+233244058519", "233244058519", "024 405 8519", n,
                  f"{n[:4]} {n[4:7]} {n[7:]}"):
        assert resolve_account(ident) == ama, ident
    with pytest.raises(AccountNotFound, match="isn't valid"):
        resolve_account(n[:-1] + str((int(n[-1]) + 1) % 10))
    with pytest.raises(AccountNotFound, match="No SokoPay account"):
        resolve_account("0200000000")
    with pytest.raises(AccountNotFound):
        resolve_account("hello")


def test_wallet_endpoint_shows_wallet_id_and_p2p_by_wallet_id(ama):
    kofi = User.objects.create_user(phone="+233200000002", full_name="Kofi Boateng")
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 100_00),
                        credit(accounts.customer_wallet(str(kofi.id)), 100_00)])
    c = APIClient()
    c.force_authenticate(user=ama)
    body = c.get("/api/v1/wallet").json()
    assert body["wallet_number"].startswith("7") and " " in body["wallet_number_display"]

    k = APIClient()
    k.force_authenticate(user=kofi)
    look = k.get("/api/v1/wallet/send/lookup", {"account": body["wallet_number"]}).json()
    assert look == {"found": True, "name": "Ama M."}
    r = k.post("/api/v1/wallet/send", {"recipient": body["wallet_number"], "amount": "15.00"}, format="json")
    assert r.status_code == 200, r.content
    assert wallet.balance(ama) == 15_00
    bad = k.post("/api/v1/wallet/send", {"recipient": "7000000000", "amount": "1.00"}, format="json")
    assert bad.status_code == 400


def test_agent_cash_in_by_wallet_id_with_name_check(ama):
    u = User.objects.create_user(phone="+233200000070")
    a = agents.register_agent(user=u, display_name="Yaw's Kiosk")
    agents.activate_agent(a)
    agents.topup_float(agent=a, amount_minor=100_00)
    n = wallet_number_for(ama)

    c = APIClient()
    c.force_authenticate(user=u)
    look = c.get("/api/v1/agent/customer", {"account": n}).json()
    assert look["name"] == "Ama M." and look["phone"] == "+23324•••8519"
    assert c.get("/api/v1/agent/customer", {"account": "0200000000"}).status_code == 404

    r = c.post("/api/v1/agent/cash-in", {"customer": n, "amount": "20.00"}, format="json")
    assert r.status_code == 201, r.content
    assert wallet.balance(ama) == 20_00
    with pytest.raises(AgentError):
        agents.cash_in(agent=a, customer_phone="7000000000", amount_minor=1_00)
