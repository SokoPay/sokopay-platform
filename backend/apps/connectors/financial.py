"""
Financial-services connectors: insurers and lenders whose products SokoPay lists and
refers customers to (the financial marketplace).

SokoPay does not underwrite insurance or lend money. It shows partner products and
passes applications (with the customer's explicit consent) to the licensed partner,
who decides. Partners must hold their own licence:
  * insurers — National Insurance Commission (NIC)
  * lenders  — a bank, savings & loans or microfinance institution licensed by BoG

Each real partner gets its own connector class, cloned from the templates below.
"""

from __future__ import annotations

import uuid

from .base import FinancialProductConnector
from .placeholder import PlaceholderFinancial
from .types import ApplicationResult, FinancialProductInfo


# --- placeholders: templates for real partners ----------------------------------
class InsurancePartnerConnector(PlaceholderFinancial):
    key = "insurance_partner"
    category = "insurance"
    display_name = "Insurance partner (template)"
    regulator = "National Insurance Commission (NIC)"
    go_live = ("Distribution/agency agreement with an NIC-licensed insurer (SokoPay may "
               "itself need NIC registration as an intermediary); product & quote API; "
               "data-sharing agreement. [VERIFY]")


class LendingPartnerConnector(PlaceholderFinancial):
    key = "lending_partner"
    category = "lending"
    display_name = "Lending partner (template)"
    regulator = "Bank of Ghana (bank, savings & loans, or microfinance)"
    go_live = ("Referral/partnership agreement with a BoG-licensed lender; application "
               "and decision API; credit-bureau consent flow; data-sharing agreement. [VERIFY]")


class SavingsPartnerConnector(PlaceholderFinancial):
    key = "savings_partner"
    category = "savings"
    display_name = "Savings partner (template)"
    regulator = "Bank of Ghana (bank or savings & loans company)"
    go_live = ("Partnership with a BoG-licensed deposit-taker; account-opening, deposit and "
               "withdrawal API (wallet <-> savings); interest disclosure; data-sharing "
               "agreement. [VERIFY]")


class InvestmentPartnerConnector(PlaceholderFinancial):
    key = "investment_partner"
    category = "investment"
    display_name = "Investment partner (template)"
    regulator = "Securities and Exchange Commission, Ghana (SEC)"
    go_live = ("Agreement with a SEC-licensed fund manager / broker (e.g. money-market or "
               "T-bill funds); subscription and redemption API; risk disclosures and "
               "suitability flow; data-sharing agreement. [VERIFY]")


class PensionPartnerConnector(PlaceholderFinancial):
    key = "pension_partner"
    category = "pension"
    display_name = "Pension partner (template)"
    regulator = "National Pensions Regulatory Authority (NPRA)"
    go_live = ("Agreement with an NPRA-licensed corporate trustee for voluntary (tier-3 / "
               "informal-sector) pensions; enrolment and contribution API; statements. [VERIFY]")


# --- dev/test doubles -------------------------------------------------------------
class _MockFinancial(FinancialProductConnector):
    available = True
    is_mock = True
    regulator = "—"
    go_live = "Never used in production."
    _products: list[FinancialProductInfo] = []

    def list_products(self):
        return list(self._products)

    def submit_application(self, request):
        return ApplicationResult(status="submitted", partner_ref=f"APP-{uuid.uuid4().hex[:10]}",
                                 message="Received by partner (mock).")

    def application_status(self, partner_ref):
        return ApplicationResult(status="pending", partner_ref=partner_ref)


class MockInsuranceConnector(_MockFinancial):
    key = "mock_insurance"
    category = "insurance"
    display_name = "Mock insurer"
    _products = [
        FinancialProductInfo(code="INS-HEALTH-BASIC", name="Basic health cover (sample)",
                             category="insurance", summary="Sample product for development.",
                             min_minor=2_000, max_minor=20_000),
        FinancialProductInfo(code="INS-FUNERAL", name="Funeral cover (sample)",
                             category="insurance", summary="Sample product for development.",
                             min_minor=1_000, max_minor=10_000),
    ]


class MockLendingConnector(_MockFinancial):
    key = "mock_lending"
    category = "lending"
    display_name = "Mock lender"
    _products = [
        FinancialProductInfo(code="LOAN-SME-STOCK", name="SME stock loan (sample)",
                             category="lending", summary="Sample product for development.",
                             min_minor=50_000, max_minor=2_000_000),
    ]
