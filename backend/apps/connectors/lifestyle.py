"""
Lifestyle partners: everyday services customers buy through SokoPay — event tickets
and food ordering. SokoPay takes the payment (wallet or MoMo); the partner fulfils.
Each real partner gets its own connector cloned from these templates.
"""

from __future__ import annotations

import secrets
import uuid

from apps.rails.types import RailStatus

from .base import LifestyleConnector
from .placeholder import PlaceholderLifestyle
from .types import ConnectorResult, Offering


class TicketingPartnerConnector(PlaceholderLifestyle):
    key = "ticketing_partner"
    category = "ticketing"
    display_name = "Event ticketing partner (template)"
    regulator = "— (commercial partner; SokoPay's own PSP licence covers the payment)"
    go_live = ("Commercial agreement with a ticketing platform; events/ticket-types API, "
               "purchase + e-ticket (QR) delivery, refunds on cancelled events. [VERIFY]")


class FoodPartnerConnector(PlaceholderLifestyle):
    key = "food_partner"
    category = "food"
    display_name = "Food ordering partner (template)"
    regulator = "— (commercial partner; SokoPay's own PSP licence covers the payment)"
    go_live = ("Commercial agreement with a food-delivery platform or restaurant network; "
               "menu/order API, order status webhooks, refunds for failed orders. [VERIFY]")


class _MockLifestyle(LifestyleConnector):
    """Dev/test double. Offering codes ending in SOLDOUT fail; tickets get a code."""

    regulator = "—"
    go_live = "Never used in production."
    available = True
    is_mock = True
    OFFERINGS: tuple = ()
    _state: dict[str, RailStatus] = {}

    def list_offerings(self):
        return list(self.OFFERINGS)

    def purchase(self, request):
        ref = f"ML-{uuid.uuid4().hex[:12]}"
        if request.offering_code.endswith("SOLDOUT"):
            self._state[ref] = RailStatus.FAILED
            return ConnectorResult(status=RailStatus.FAILED, provider_ref=ref, failure_code="sold_out")
        self._state[ref] = RailStatus.SUCCEEDED
        token = "-".join(secrets.token_hex(2).upper() for _ in range(3)) if self.category == "ticketing" else ""
        return ConnectorResult(status=RailStatus.SUCCEEDED, provider_ref=ref, token=token)

    def get_status(self, provider_ref):
        return ConnectorResult(status=self._state.get(provider_ref, RailStatus.UNKNOWN), provider_ref=provider_ref)


class MockTicketingConnector(_MockLifestyle):
    key = "mock_ticketing"
    category = "ticketing"
    display_name = "Sample events (test)"
    OFFERINGS = (
        Offering("AFRO-REG", "Afrobeats Night, Accra: Regular", 150_00, "Sat 8pm, Accra Sports Stadium"),
        Offering("AFRO-VIP", "Afrobeats Night, Accra: VIP", 400_00, "Sat 8pm, front stage"),
        Offering("KOTOKO-HEARTS", "Kotoko vs Hearts: Popular stand", 50_00, "Sun 3pm, Baba Yara"),
        Offering("GONE-SOLDOUT", "Sold-out show (test)", 100_00, "Always fails, for testing"),
    )


class MockFoodConnector(_MockLifestyle):
    key = "mock_food"
    category = "food"
    display_name = "Sample restaurant (test)"
    OFFERINGS = (
        Offering("JOLLOF-CHK", "Jollof rice with chicken", 65_00, "Delivered in about 40 min"),
        Offering("WAAKYE", "Waakye special", 45_00, "With egg, wele and gari"),
        Offering("KELEWELE", "Kelewele and groundnuts", 25_00, "Spicy fried plantain"),
    )
