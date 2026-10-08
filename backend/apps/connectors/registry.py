"""
Connector registry and routing.

* get_connector(category, key) — one connector by key.
* biller_connector(biller)       — the connector a Biller row is routed to.
* telco_connector(network)       — per settings.TELCO_ROUTES (default "rail").
* transfer_connector(destination)— per settings.INTEROP_ROUTES.
* catalogue()                    — every connector with its status, regulator and
                                   go-live requirements (shown in the admin portal).
"""

from __future__ import annotations

from django.conf import settings

from . import (banks, billers, cards, cross_border, financial, identity, lifestyle, remittance,
               telcos, transfers)
from .base import Connector
from .types import TransferDestination


class UnknownConnector(KeyError):
    pass


BILLERS: dict[str, type[Connector]] = {
    "rail": billers.RailBillerConnector,
    "ecg": billers.ECGConnector,
    "nedco": billers.NEDCoConnector,
    "gwcl": billers.GhanaWaterConnector,
    "telecel_fibre": billers.TelecelFibreConnector,
    "dstv": billers.DStvConnector,
    "gotv": billers.GOtvConnector,
    "startimes": billers.StarTimesConnector,
}

TELCOS: dict[str, type[Connector]] = {
    "rail": telcos.RailTelcoConnector,
    "mtn": telcos.MTNTelcoConnector,
    "telecel": telcos.TelecelTelcoConnector,
    "at": telcos.ATTelcoConnector,
}

TRANSFERS: dict[str, type[Connector]] = {
    "rail": transfers.RailTransferConnector,
    "mock": transfers.MockTransferConnector,
    "ghipss_mmi": transfers.GhipssMmiConnector,
    "ghipss_gip": transfers.GhipssGipConnector,
    "mtn_momo": transfers.MtnMomoConnector,
    "telecel_cash": transfers.TelecelCashConnector,
    "at_money": transfers.AtMoneyConnector,
    "gmoney": transfers.GMoneyConnector,
    "zeepay": transfers.ZeepayConnector,
}

CARDS: dict[str, type[Connector]] = {
    "visa": cards.VisaConnector,
    "mastercard": cards.MastercardConnector,
    "ghlink": cards.GhLinkConnector,
}

FINANCIAL: dict[str, type[Connector]] = {
    "insurance_partner": financial.InsurancePartnerConnector,
    "lending_partner": financial.LendingPartnerConnector,
    "savings_partner": financial.SavingsPartnerConnector,
    "investment_partner": financial.InvestmentPartnerConnector,
    "pension_partner": financial.PensionPartnerConnector,
    "mock_insurance": financial.MockInsuranceConnector,
    "mock_lending": financial.MockLendingConnector,
}

IDENTITY: dict[str, type[Connector]] = {
    "nia": identity.NiaConnector,
    "kyc_provider": identity.KycProviderConnector,
    "mock": identity.MockIdentityConnector,
}

BANKS: dict[str, type[Connector]] = {
    "trust_bank_api": banks.TrustBankApiConnector,
}

REMITTANCE: dict[str, type[Connector]] = {
    "remittance_partner": remittance.RemittancePartnerConnector,
    "mock": remittance.MockRemittanceConnector,
}

LIFESTYLE: dict[str, type[Connector]] = {
    "ticketing_partner": lifestyle.TicketingPartnerConnector,
    "food_partner": lifestyle.FoodPartnerConnector,
    "mock_ticketing": lifestyle.MockTicketingConnector,
    "mock_food": lifestyle.MockFoodConnector,
}

CROSS_BORDER: dict[str, type[Connector]] = {
    "onafriq": cross_border.OnafriqConnector,
    "brij": cross_border.BrijConnector,
    "mock": cross_border.MockCrossBorderConnector,
}

REGISTRY = {
    "biller": BILLERS,
    "telco": TELCOS,
    "transfer": TRANSFERS,
    "card": CARDS,
    "financial": FINANCIAL,
    "identity": IDENTITY,
    "bank": BANKS,
    "remittance": REMITTANCE,
    "lifestyle": LIFESTYLE,
    "cross_border": CROSS_BORDER,
}


def get_connector(category: str, key: str, **kwargs) -> Connector:
    try:
        cls = REGISTRY[category][key]
    except KeyError as exc:
        raise UnknownConnector(f"No {category} connector registered as {key!r}.") from exc
    # Test doubles are invisible unless the deployment explicitly allows them, so a
    # production box can never be talked into using "mock" by a URL or an env var.
    if cls.is_mock and not getattr(settings, "ALLOW_MOCK_INTEGRATIONS", False):
        raise UnknownConnector(f"No {category} connector registered as {key!r}.")
    return cls(**kwargs)


def biller_connector(biller, rail_name: str | None = None):
    key = getattr(biller, "connector", "") or "rail"
    if key == "rail":
        return billers.RailBillerConnector(rail_name=rail_name)
    return get_connector("biller", key)


def telco_connector(network: str, rail_name: str | None = None):
    key = getattr(settings, "TELCO_ROUTES", {}).get(network, "rail")
    if key == "rail":
        return telcos.RailTelcoConnector(network=network, rail_name=rail_name)
    return get_connector("telco", key)


def transfer_connector(destination: TransferDestination):
    """Most specific route wins: '<type>:<institution>' then '<type>'."""
    routes = getattr(settings, "INTEROP_ROUTES", {})
    key = (routes.get(f"{destination.type}:{destination.institution}")
           or routes.get(str(destination.type))
           or "rail")
    return get_connector("transfer", key)


def identity_connector():
    return get_connector("identity", getattr(settings, "KYC_IDENTITY_PROVIDER", "nia"))


def remittance_connector(partner: str):
    return get_connector("remittance", partner)


def catalogue() -> list[dict]:
    """Every registered connector and its readiness, for ops/compliance."""
    rows = []
    for category, entries in REGISTRY.items():
        for key, cls in entries.items():
            rows.append({
                "category": cls.category or category,
                "key": key,
                "name": cls.display_name,
                "regulator": cls.regulator,
                "status": ("mock (dev/test only)" if cls.is_mock
                           else "available" if cls.available else "placeholder"),
                "go_live": cls.go_live,
            })
    return rows
