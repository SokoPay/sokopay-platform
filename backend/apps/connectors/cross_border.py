"""
Cross-border partners: sending money from Ghana to other countries.

SokoPay doesn't move money across borders itself. A licensed partner quotes the rate
and fee, takes the cedis from SokoPay's settlement account, does the FX, and pays out
in the destination country (mobile money or bank). PAPSS (via a partner bank) is the
separate pan-African settlement route — see LICENCE-CAPABILITY-MATRIX.md.

Licence: outbound cross-border from a SokoPay wallet needs BoG approval
(CROSS_BORDER_TRANSFER). Corridors below are illustrative until contracted. [VERIFY]
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import ROUND_DOWN, Decimal

from django.utils import timezone

from apps.rails.types import RailStatus

from .base import CrossBorderConnector
from .placeholder import PlaceholderCrossBorder
from .types import ConnectorResult, CrossBorderQuote


class OnafriqConnector(PlaceholderCrossBorder):
    key = "onafriq"
    display_name = "Onafriq (formerly MFS Africa)"
    regulator = "Bank of Ghana approval for the corridor; Onafriq licensed in each market"
    go_live = ("Commercial agreement with Onafriq; quote/send/status API keys and webhook "
               "secret; corridor list and limits; prefunding/settlement account; AML "
               "sanctions screening responsibilities agreed. [VERIFY]")
    corridors = frozenset({"NG", "KE", "UG", "TZ", "CI", "SN", "CM", "ZM", "RW"})


class BrijConnector(PlaceholderCrossBorder):
    key = "brij"
    display_name = "Brij"
    regulator = "Bank of Ghana approval for the corridor; partner licensing per market [VERIFY]"
    go_live = ("Commercial agreement with Brij; API documentation and sandbox keys; "
               "supported corridors and limits; settlement arrangement; AML/sanctions "
               "screening responsibilities agreed. [VERIFY]")
    corridors = frozenset()          # confirm with Brij


class MockCrossBorderConnector(CrossBorderConnector):
    """Dev/test double: sample rates, 1.5% fee (min GH₵2), scriptable outcome."""

    key = "mock"
    display_name = "Mock cross-border partner"
    regulator = "—"
    go_live = "Never used in production."
    available = True
    is_mock = True
    corridors = frozenset({"NG", "KE", "CI"})
    RATES = {"NG": ("NGN", Decimal("105.50")), "KE": ("KES", Decimal("8.90")), "CI": ("XOF", Decimal("41.20"))}

    _state: dict[str, RailStatus] = {}
    _next: RailStatus = RailStatus.PENDING

    @classmethod
    def reset(cls) -> None:
        cls._state.clear()
        cls._next = RailStatus.PENDING

    @classmethod
    def script(cls, status: RailStatus) -> None:
        cls._next = status

    @classmethod
    def drive(cls, ref: str, status: RailStatus) -> None:
        cls._state[ref] = status

    def quote(self, send_minor, recipient):
        currency, rate = self.RATES[recipient.country]
        fee = max((send_minor * 150 + 5000) // 10000, 200)
        receive = (Decimal(send_minor) / 100 * rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return CrossBorderQuote(quote_id=f"MQ-{uuid.uuid4().hex[:12]}", send_minor=send_minor + fee, fee_minor=fee,
                                receive_amount=str(receive), receive_currency=currency, rate=str(rate),
                                expires_at=(timezone.now() + dt.timedelta(minutes=10)).isoformat())

    def send(self, request):
        ref = f"MXB-{uuid.uuid4().hex[:14]}"
        status = RailStatus.FAILED if request.recipient.account.endswith("0000") else self._next
        self._state[ref] = status
        return ConnectorResult(status=status, provider_ref=ref,
                               failure_code="account_not_found" if status == RailStatus.FAILED else "")

    def get_status(self, provider_ref):
        return ConnectorResult(status=self._state.get(provider_ref, RailStatus.UNKNOWN), provider_ref=provider_ref)
