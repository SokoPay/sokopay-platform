"""
Placeholder behaviour shared by every not-yet-integrated connector.

A placeholder implements its whole interface, but every operation raises
ConnectorNotImplemented with a message saying which party it is and what is needed to
go live. That makes the gap explicit and greppable, and impossible to mistake for a
working integration.
"""

from __future__ import annotations

from .base import (
    BillerConnector,
    CardSchemeConnector,
    CrossBorderConnector,
    FinancialProductConnector,
    IdentityConnector,
    LifestyleConnector,
    RemittanceConnector,
    TelcoConnector,
    TransferConnector,
    TrustBankConnector,
)


class ConnectorNotImplemented(NotImplementedError):
    def __init__(self, connector, operation: str):
        self.connector_key = connector.key
        self.operation = operation
        super().__init__(
            f"{connector.display_name} [{connector.key}] '{operation}' is a placeholder. "
            f"To go live: {connector.go_live or 'agreement, API docs and credentials.'}"
        )


class _Placeholder:
    available = False

    def _todo(self, operation: str):
        raise ConnectorNotImplemented(self, operation)


class PlaceholderBiller(_Placeholder, BillerConnector):
    def lookup_account(self, biller_code, account):
        self._todo("lookup_account")

    def pay(self, request):
        self._todo("pay")

    def get_status(self, provider_ref):
        self._todo("get_status")


class PlaceholderTelco(_Placeholder, TelcoConnector):
    def list_bundles(self):
        self._todo("list_bundles")

    def topup_airtime(self, request):
        self._todo("topup_airtime")

    def buy_bundle(self, request):
        self._todo("buy_bundle")

    def get_status(self, provider_ref):
        self._todo("get_status")


class PlaceholderTransfer(_Placeholder, TransferConnector):
    def name_enquiry(self, destination):
        self._todo("name_enquiry")

    def send(self, request):
        self._todo("send")

    def get_status(self, provider_ref):
        self._todo("get_status")


class PlaceholderCardScheme(_Placeholder, CardSchemeConnector):
    def create_payment_session(self, request):
        self._todo("create_payment_session")

    def push_to_card(self, request):
        self._todo("push_to_card")

    def get_status(self, provider_ref):
        self._todo("get_status")


class PlaceholderFinancial(_Placeholder, FinancialProductConnector):
    def list_products(self):
        self._todo("list_products")

    def submit_application(self, request):
        self._todo("submit_application")

    def application_status(self, partner_ref):
        self._todo("application_status")


class PlaceholderIdentity(_Placeholder, IdentityConnector):
    def verify_ghana_card(self, card_number, phone):
        self._todo("verify_ghana_card")

    def verify_liveness(self, selfie, card_number):
        self._todo("verify_liveness")


class PlaceholderTrustBank(_Placeholder, TrustBankConnector):
    def get_balance(self, account_number):
        self._todo("get_balance")


class PlaceholderRemittance(_Placeholder, RemittanceConnector):
    def verify_and_parse(self, headers, body):
        self._todo("verify_and_parse")


class PlaceholderLifestyle(_Placeholder, LifestyleConnector):
    def list_offerings(self):
        self._todo("list_offerings")

    def purchase(self, request):
        self._todo("purchase")

    def get_status(self, provider_ref):
        self._todo("get_status")


class PlaceholderCrossBorder(_Placeholder, CrossBorderConnector):
    def quote(self, send_minor, recipient):
        self._todo("quote")

    def send(self, request):
        self._todo("send")

    def get_status(self, provider_ref):
        self._todo("get_status")
