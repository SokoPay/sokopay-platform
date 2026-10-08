"""
Biller connectors.

Default route: "rail" — bills are paid THROUGH the Enhanced PSP partner (Korba/Nsano),
which already holds the biller integrations. This is how SokoPay pays billers under a
PSP Standard/Medium licence, and it works today (against the mock rail in dev).

Direct routes: one placeholder per biller, for when SokoPay signs a direct agreement
(usually at PSP Enhanced). Switching a biller over is a data change — set
Biller.connector from "rail" to e.g. "ecg" — once that connector is implemented.
"""

from __future__ import annotations

from apps.rails.registry import get_rail

from .base import BillerConnector
from .placeholder import PlaceholderBiller
from .types import ConnectorResult


def _from_rail(result) -> ConnectorResult:
    return ConnectorResult(
        status=result.status, provider_ref=result.provider_ref, message=result.message,
        failure_code=result.failure_code, raw=result.raw,
    )


class RailBillerConnector(BillerConnector):
    key = "rail"
    display_name = "Via payment partner (Enhanced PSP)"
    regulator = "Bank of Ghana — partner holds an Enhanced PSP licence"
    go_live = "Enhanced PSP agreement; biller codes from the partner's biller catalogue."
    available = True

    def __init__(self, rail_name: str | None = None) -> None:
        self.rail_name = rail_name

    def _rail(self):
        return get_rail(self.rail_name)

    def lookup_account(self, biller_code, account):
        return self._rail().lookup_biller_account(biller_code, account)

    def pay(self, request):
        return _from_rail(self._rail().pay_bill(request))

    def get_status(self, provider_ref):
        return _from_rail(self._rail().get_status(provider_ref))


# --- direct-integration placeholders ------------------------------------------
class ECGConnector(PlaceholderBiller):
    key = "ecg"
    display_name = "ECG — Electricity Company of Ghana (prepaid & postpaid)"
    regulator = "PURC / Energy Commission (utility)"
    go_live = ("Direct vending agreement with ECG; vending API docs and credentials; "
               "token delivery format for prepaid meters. [VERIFY]")


class NEDCoConnector(PlaceholderBiller):
    key = "nedco"
    display_name = "NEDCo — Northern Electricity Distribution Company"
    regulator = "PURC / Energy Commission (utility)"
    go_live = "Direct agreement with NEDCo; API docs and credentials. [VERIFY]"


class GhanaWaterConnector(PlaceholderBiller):
    key = "gwcl"
    display_name = "Ghana Water Limited"
    regulator = "PURC (utility)"
    go_live = "Collection agreement with Ghana Water; bill enquiry/payment API. [VERIFY]"


class TelecelFibreConnector(PlaceholderBiller):
    key = "telecel_fibre"
    display_name = "Telecel Fibre / Broadband (formerly Vodafone Fibre)"
    regulator = "National Communications Authority (telco)"
    go_live = "Biller agreement with Telecel Ghana; account enquiry/payment API. [VERIFY]"


class DStvConnector(PlaceholderBiller):
    key = "dstv"
    display_name = "DStv (MultiChoice Ghana)"
    regulator = "National Communications Authority (pay-TV)"
    go_live = ("Agent/biller agreement with MultiChoice Ghana; smartcard lookup, "
               "bouquet list and payment APIs. [VERIFY]")


class GOtvConnector(PlaceholderBiller):
    key = "gotv"
    display_name = "GOtv (MultiChoice Ghana)"
    regulator = "National Communications Authority (pay-TV)"
    go_live = "Same MultiChoice agreement as DStv; IUC lookup and payment APIs. [VERIFY]"


class StarTimesConnector(PlaceholderBiller):
    key = "startimes"
    display_name = "StarTimes Ghana"
    regulator = "National Communications Authority (pay-TV)"
    go_live = "Biller agreement with StarTimes Ghana; smartcard lookup and payment APIs. [VERIFY]"
