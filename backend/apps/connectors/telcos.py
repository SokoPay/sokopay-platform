"""
Telco connectors: airtime top-up and data bundles for MTN, Telecel and AT
(formerly AirtelTigo).

Default route: "rail" — airtime and data are sold THROUGH the Enhanced PSP partner,
which resells them from the telcos. Works today against the mock rail.

Direct routes: one placeholder per telco for a direct reseller/distributor agreement.

The bundle list on the rail route is a SAMPLE catalogue (flagged is_sample=True) so the
apps can be built and tested. It must be replaced by the live catalogue from the
partner or telco before launch — real bundle names, volumes and prices change often.
"""

from __future__ import annotations

from apps.common.money import Money
from apps.rails.registry import get_rail
from apps.rails.types import BillRequest

from .base import TelcoConnector
from .billers import _from_rail
from .placeholder import PlaceholderTelco
from .types import Bundle

NETWORK_NAMES = {"mtn": "MTN", "telecel": "Telecel", "at": "AT (formerly AirtelTigo)"}


def _sample_bundles(network: str) -> list[Bundle]:
    """Placeholder bundle catalogue. NOT real telco prices. [VERIFY → replace with live list]"""
    rows = [
        ("D1", "Daily 500MB", 500, "500 MB", "1 day"),
        ("W2", "Weekly 2GB", 1500, "2 GB", "7 days"),
        ("M10", "Monthly 10GB", 6000, "10 GB", "30 days"),
    ]
    return [
        Bundle(code=f"{network.upper()}-{code}", name=f"{NETWORK_NAMES[network]} {name}",
               network=network, price_minor=price, volume=volume, validity=validity,
               is_sample=True)
        for code, name, price, volume, validity in rows
    ]


class RailTelcoConnector(TelcoConnector):
    """Airtime/data through the Enhanced PSP partner (one instance per network)."""

    key = "rail"
    display_name = "Via payment partner (Enhanced PSP)"
    regulator = "Bank of Ghana — partner holds an Enhanced PSP licence"
    go_live = "Enhanced PSP agreement; partner's airtime/data product codes and live bundle list."
    available = True

    def __init__(self, network: str, rail_name: str | None = None) -> None:
        self.network = network
        self.rail_name = rail_name

    def _rail(self):
        return get_rail(self.rail_name)

    def list_bundles(self):
        return _sample_bundles(self.network)

    def topup_airtime(self, request):
        return _from_rail(self._rail().pay_bill(BillRequest(
            amount=request.amount,
            biller_code=f"{self.network}_airtime",
            account=request.phone,
            reference=request.reference,
            idempotency_key=request.idempotency_key,
        )))

    def buy_bundle(self, request):
        return _from_rail(self._rail().pay_bill(BillRequest(
            amount=Money(request.bundle.price_minor, "GHS"),
            biller_code=f"{self.network}_data:{request.bundle.code}",
            account=request.phone,
            reference=request.reference,
            idempotency_key=request.idempotency_key,
        )))

    def get_status(self, provider_ref):
        return _from_rail(self._rail().get_status(provider_ref))


# --- direct-integration placeholders ------------------------------------------
class MTNTelcoConnector(PlaceholderTelco):
    key = "mtn"
    network = "mtn"
    display_name = "MTN Ghana — airtime & data (direct)"
    regulator = "National Communications Authority"
    go_live = "Airtime/data distributor agreement with MTN Ghana; reseller API and float. [VERIFY]"


class TelecelTelcoConnector(PlaceholderTelco):
    key = "telecel"
    network = "telecel"
    display_name = "Telecel Ghana — airtime & data (direct)"
    regulator = "National Communications Authority"
    go_live = "Distributor agreement with Telecel Ghana; reseller API and float. [VERIFY]"


class ATTelcoConnector(PlaceholderTelco):
    key = "at"
    network = "at"
    display_name = "AT Ghana (formerly AirtelTigo) — airtime & data (direct)"
    regulator = "National Communications Authority"
    go_live = "Distributor agreement with AT Ghana; reseller API and float. [VERIFY]"
