"""Cross-border send: licence, KYC, quote binding, PIN, sanctions, reserve and settle/refund."""

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Sum
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.compliance.models import Alert, WatchlistEntry
from apps.compliance.screening import normalise
from apps.connectors.cross_border import MockCrossBorderConnector
from apps.kyc.models import KycProfile
from apps.ledger import accounts
from apps.ledger.models import Posting
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.gate import _enabled_set
from apps.rails.types import RailStatus
from apps.wallet import cross_border as xb
from apps.wallet.models import CrossBorderTransfer

User = get_user_model()
pytestmark = pytest.mark.django_db
PIN = "428173"
RECIPIENT = {"country": "NG", "method": "mobile_money", "account": "+2348031234567", "institution": "mtn_ng",
             "name": "Chidi Okafor"}


@pytest.fixture(autouse=True)
def _enhanced(settings):
    settings.SOKOPAY_ACTIVE_LICENCE = "DEMI"
    settings.CROSS_BORDER_PROVIDER = "mock"
    settings.ALLOW_MOCK_INTEGRATIONS = True
    MockCrossBorderConnector.reset()
    _enabled_set.cache_clear()
    yield
    _enabled_set.cache_clear()


@pytest.fixture
def kofi(db):
    u = User.objects.create_user(phone="+233244058519", full_name="Kofi Mensah")
    u.set_pin(PIN)
    u.save()
    KycProfile.objects.update_or_create(user=u, defaults={"tier": 1})
    post_entry("seed", [debit(accounts.partner_clearing("mock"), 1000_00),
                        credit(accounts.customer_wallet(str(u.id)), 1000_00)])
    return u


def _wallet(u):
    return natural_balance_of(accounts.customer_wallet(str(u.id)))


def _books_zero():
    assert (Posting.objects.aggregate(s=Sum("amount"))["s"] or 0) == 0


def test_quote_send_pending_then_delivered(kofi):
    q = xb.get_quote(user=kofi, amount_minor=200_00, **RECIPIENT)
    assert q["receive_currency"] == "NGN" and q["total_minor"] == 200_00 + 3_00
    t = xb.send(user=kofi, quote_id=q["quote_id"], purpose="family_support", pin=PIN)
    assert t.status == "pending" and _wallet(kofi) == 1000_00 - 203_00
    assert natural_balance_of(accounts.cross_border_in_flight()) == 203_00
    with pytest.raises(xb.CrossBorderError, match="expired|already"):     # quote is single-use
        xb.send(user=kofi, quote_id=q["quote_id"], purpose="family_support", pin=PIN)
    MockCrossBorderConnector.drive(t.provider_ref, RailStatus.SUCCEEDED)
    xb.resolve_pending()
    t.refresh_from_db()
    assert t.status == "succeeded" and natural_balance_of(accounts.cross_border_in_flight()) == 0
    _books_zero()


def test_refused_send_is_refunded(kofi):
    q = xb.get_quote(user=kofi, amount_minor=50_00, **{**RECIPIENT, "account": "+2348030000000"})
    t = xb.send(user=kofi, quote_id=q["quote_id"], purpose="gift", pin=PIN)
    assert t.status == "failed" and _wallet(kofi) == 1000_00
    _books_zero()


def test_guards(kofi):
    stranger = User.objects.create_user(phone="+233244000777", full_name="Ama Owusu")
    stranger.set_pin(PIN)
    stranger.save()
    with pytest.raises(xb.CrossBorderError, match="Ghana Card"):
        xb.get_quote(user=stranger, amount_minor=50_00, **RECIPIENT)
    with pytest.raises(xb.CrossBorderError, match="country"):
        xb.get_quote(user=kofi, amount_minor=50_00, **{**RECIPIENT, "country": "US"})
    with pytest.raises(xb.CrossBorderError, match="Send between"):
        xb.get_quote(user=kofi, amount_minor=1_00, **RECIPIENT)
    q = xb.get_quote(user=kofi, amount_minor=50_00, **RECIPIENT)
    KycProfile.objects.update_or_create(user=stranger, defaults={"tier": 1})
    with pytest.raises(xb.CrossBorderError, match="expired"):            # someone else's quote
        xb.send(user=stranger, quote_id=q["quote_id"], purpose="gift", pin=PIN)
    with pytest.raises(xb.CrossBorderError):                              # wrong PIN
        xb.send(user=kofi, quote_id=q["quote_id"], purpose="gift", pin="000000")
    assert not CrossBorderTransfer.objects.exists()


def test_sanctioned_recipient_refused_with_alert_and_no_money_moves(kofi):
    WatchlistEntry.objects.create(source="un", kind="sanctions", name="Chidi Okafor",
                                  normalised=[normalise("Chidi Okafor")])
    q = xb.get_quote(user=kofi, amount_minor=50_00, **RECIPIENT)
    with pytest.raises(xb.CrossBorderError, match="Contact SokoPay support"):
        xb.send(user=kofi, quote_id=q["quote_id"], purpose="gift", pin=PIN)
    assert Alert.objects.filter(rule="SANCTIONS_MATCH").exists()
    assert _wallet(kofi) == 1000_00 and not CrossBorderTransfer.objects.exists()


def test_not_offered_until_a_partner_is_configured(kofi, settings):
    settings.CROSS_BORDER_PROVIDER = ""
    assert xb.corridors()["available"] is False
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(kofi)['access']}")
    assert c.get("/api/v1/cross-border").json()["available"] is False


def test_api_round_trip(kofi):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.issue_tokens(kofi)['access']}")
    assert "NG" in c.get("/api/v1/cross-border").json()["countries"]
    q = c.post("/api/v1/cross-border/quote", {**RECIPIENT, "amount": "100.00"}, format="json")
    assert q.status_code == 200, q.content
    s = c.post("/api/v1/cross-border/send", {"quote_id": q.json()["quote_id"], "purpose": "education", "pin": PIN},
               format="json")
    assert s.status_code == 201 and s.json()["reference"].startswith("XB-")
    assert len(c.get("/api/v1/cross-border/transfers").json()["results"]) == 1
