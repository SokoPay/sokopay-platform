"""Connector registry: placeholders refuse clearly, routing picks the right connector."""

import pytest

from apps.connectors import registry
from apps.connectors.placeholder import ConnectorNotImplemented
from apps.connectors.types import DestinationType, TransferDestination

# One operation per category to prove every placeholder refuses.
_PROBE = {
    "biller": lambda c: c.pay(None),
    "telco": lambda c: c.list_bundles(),
    "transfer": lambda c: c.send(None),
    "card": lambda c: c.push_to_card(None),
    "financial": lambda c: c.list_products(),
    "identity": lambda c: c.verify_ghana_card("GHA-123456789-0", "+233244058519"),
    "bank": lambda c: c.get_balance("0000"),
    "remittance": lambda c: c.verify_and_parse({}, b"{}"),
    "lifestyle": lambda c: c.list_offerings(),
    "cross_border": lambda c: c.quote(100_00, None),
}


def test_new_aggregation_rails_are_catalogued_as_placeholders():
    rows = {r["key"]: r for r in registry.catalogue()}
    for key, category in (("savings_partner", "savings"), ("investment_partner", "investment"),
                          ("pension_partner", "pension"), ("ticketing_partner", "ticketing"),
                          ("food_partner", "food"), ("onafriq", "cross_border"),
                          ("brij", "cross_border")):
        assert rows[key]["category"] == category and rows[key]["status"] == "placeholder"
        assert rows[key]["go_live"] and rows[key]["regulator"]
    assert "NG" in registry.get_connector("cross_border", "onafriq").corridors

PLACEHOLDERS = [
    (category, key, cls)
    for category, entries in registry.REGISTRY.items()
    for key, cls in entries.items()
    if not cls.available
]


@pytest.mark.parametrize("category,key,cls", PLACEHOLDERS, ids=[p[1] for p in PLACEHOLDERS])
def test_every_placeholder_refuses_with_go_live_guidance(category, key, cls):
    connector = cls()
    with pytest.raises(ConnectorNotImplemented) as exc:
        _PROBE[category](connector)
    message = str(exc.value)
    assert key in message
    assert "To go live" in message
    assert connector.go_live, f"{key} must document what is needed to go live"


def test_requested_providers_are_registered():
    expected = {
        "biller": {"ecg", "gwcl", "telecel_fibre", "dstv", "gotv", "startimes", "nedco"},
        "telco": {"mtn", "telecel", "at"},
        "transfer": {"ghipss_mmi", "ghipss_gip", "mtn_momo", "telecel_cash", "at_money",
                     "gmoney", "zeepay"},
        "card": {"visa", "mastercard", "ghlink"},
        "financial": {"insurance_partner", "lending_partner"},
    }
    for category, keys in expected.items():
        assert keys <= set(registry.REGISTRY[category]), category


def test_catalogue_statuses():
    by_key = {(r["category"], r["key"]): r["status"] for r in registry.catalogue()}
    assert by_key[("biller", "rail")] == "available"
    assert by_key[("biller", "ecg")] == "placeholder"
    assert by_key[("transfer", "mock")] == "mock (dev/test only)"
    assert by_key[("card", "visa")] == "placeholder"


def test_unknown_connector():
    with pytest.raises(registry.UnknownConnector):
        registry.get_connector("biller", "does_not_exist")


def test_transfer_routing_most_specific_wins(settings):
    settings.INTEROP_ROUTES = {"wallet": "ghipss_mmi", "wallet:zeepay": "zeepay", "momo": "rail"}
    zeepay = TransferDestination(DestinationType.WALLET, "zeepay", "123")
    gmoney = TransferDestination(DestinationType.WALLET, "gmoney", "123")
    momo = TransferDestination(DestinationType.MOMO, "mtn", "+233244058519")
    assert registry.transfer_connector(zeepay).key == "zeepay"
    assert registry.transfer_connector(gmoney).key == "ghipss_mmi"
    assert registry.transfer_connector(momo).key == "rail"
