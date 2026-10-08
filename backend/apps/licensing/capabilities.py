"""
The licence-capability matrix: the heart of "build everything now, switch on per
licence".

HOW IT WORKS
------------
Every regulated activity SokoPay can perform is named as a `Capability`. Each Bank
of Ghana licence tier is mapped to the exact set of capabilities it permits. The
deployment's active licence (settings.SOKOPAY_ACTIVE_LICENCE) therefore resolves to
a concrete set of allowed capabilities.

Feature code never asks "which licence do we have?". It asks "are we allowed to do
X?" via `is_enabled(Capability.X)` or the `require_capability` guard. This keeps the
regulatory rules in ONE place and lets us build wallet, agent and cross-border
features today, shipped safely dark, and turn each on the day its licence is granted
by changing a single environment variable.

SOURCE
------
Mappings follow the BoG "Licence Categories & Permissible Activities" chart and the
"Licensing Requirements for DEMIs, PSPs and PFTSPs" document (both in ../docs).
Lines marked [VERIFY] should be confirmed with BoG / legal counsel before the
corresponding licence goes live, because the chart leaves some edges to interpretation.
"""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    # --- PSP Standard: a payment app connected to an Enhanced PSP, risk upstream ---
    BILL_PAYMENT = "bill_payment"                 # pay ECG, water, TV, etc.
    AIRTIME_DATA = "airtime_data"                 # buy airtime / data bundles
    MERCHANT_CHECKOUT_UPSTREAM = "merchant_checkout_upstream"  # pay a merchant; liability sits with the Enhanced PSP

    # --- PSP Medium: aggregation and merchant infrastructure ---
    MERCHANT_AGGREGATION = "merchant_aggregation"   # onboard & collect for merchants
    BILLER_AGGREGATION = "biller_aggregation"       # onboard & route to billers
    POS_DEPLOYMENT = "pos_deployment"               # deploy POS / SoftPOS
    CARD_ACQUIRING = "card_acquiring"               # accept Visa/Mastercard/GH-Link (via partner-hosted fields)
    NON_CASH_INSTRUMENTS = "non_cash_instruments"   # cheques, prepaid instruments
    SETTLEMENT_TO_MERCHANT = "settlement_to_merchant"  # pay merchants their balance
    # Merchants paying many recipients from their balance (payroll, suppliers), routed
    # through the Enhanced PSP partner's disbursement rails. [VERIFY tier with BoG]
    BULK_DISBURSEMENT = "bulk_disbursement"

    # --- PSP Enhanced: own processing and gateway ---
    PAYMENT_PROCESSING = "payment_processing"       # process on our own rails
    THIRD_PARTY_GATEWAY = "third_party_gateway"     # provide a gateway to others
    INWARD_REMITTANCE = "inward_remittance"         # inward international remittance service
    EMV_CARD_ISSUING = "emv_card_issuing"           # print/personalise EMV cards
    CLOSED_LOOP_VCARD = "closed_loop_vcard"         # limited-use closed-loop virtual cards

    # --- EMI / DEMI: holding customer money and the agent economy ---
    HOLD_CUSTOMER_FUNDS = "hold_customer_funds"     # issue e-money / maintain a wallet balance
    WALLET_P2P = "wallet_p2p"                        # on-net / off-net person-to-person
    CASH_IN_OUT = "cash_in_out"                      # cash-in / cash-out
    AGENT_NETWORK = "agent_network"                  # recruit & manage agents
    WALLET_BANK_TRANSFER = "wallet_bank_transfer"    # wallet ↔ bank account transfers
    INBOUND_REMITTANCE_TERMINATION = "inbound_remittance_termination"

    # --- Cross-border (Enhanced+ and partner-bank mediated; see note below) ---
    CROSS_BORDER_PAPSS = "cross_border_papss"        # pan-African settlement via PAPSS
    # Customers sending money abroad via licensed partners (Onafriq, Brij). [VERIFY tier]
    CROSS_BORDER_TRANSFER = "cross_border_transfer"

    # --- Financial-services aggregation (insurance, lending) ---
    # Enhanced: "marketplace for financial services offered by duly regulated
    # financial service providers" — listing products and passing applications on.
    FINANCIAL_MARKETPLACE = "financial_marketplace"
    # DEMI: "investment, savings, credit, insurance and pension products ONLY in
    # partnership with banks and duly regulated institutions" — e.g. loan
    # disbursed into the wallet, premium paid from it.
    EMBEDDED_FINANCIAL_PRODUCTS = "embedded_financial_products"

    def __str__(self) -> str:  # so it prints as the plain value in errors/logs
        return self.value


class Licence(str, Enum):
    PSP_STANDARD = "PSP_STANDARD"
    PSP_MEDIUM = "PSP_MEDIUM"
    PSP_ENHANCED = "PSP_ENHANCED"
    EMI = "EMI"      # Payment Service Provider (Scheme) / e-money scheme tier
    DEMI = "DEMI"    # Dedicated Electronic Money Issuer

    def __str__(self) -> str:
        return self.value


# Capabilities granted *newly* at each tier. The effective set for a licence is the
# union of its own grants plus every tier it builds on (see TIER_ORDER below). We
# express it incrementally so the mapping is easy to read and audit against the BoG
# chart.
_GRANTS: dict[Licence, set[Capability]] = {
    Licence.PSP_STANDARD: {
        Capability.BILL_PAYMENT,
        Capability.AIRTIME_DATA,
        Capability.MERCHANT_CHECKOUT_UPSTREAM,
    },
    Licence.PSP_MEDIUM: {
        Capability.MERCHANT_AGGREGATION,
        Capability.BILLER_AGGREGATION,
        Capability.POS_DEPLOYMENT,
        Capability.CARD_ACQUIRING,
        Capability.NON_CASH_INSTRUMENTS,
        Capability.SETTLEMENT_TO_MERCHANT,
        Capability.BULK_DISBURSEMENT,
    },
    Licence.PSP_ENHANCED: {
        Capability.PAYMENT_PROCESSING,
        Capability.THIRD_PARTY_GATEWAY,
        Capability.INWARD_REMITTANCE,
        Capability.EMV_CARD_ISSUING,
        Capability.CLOSED_LOOP_VCARD,
        # PAPSS access is realistically an Enhanced-tier activity, routed through a
        # partner bank that participates in PAPSS. [VERIFY route with partner bank/BoG]
        Capability.CROSS_BORDER_PAPSS,
        Capability.CROSS_BORDER_TRANSFER,
        Capability.FINANCIAL_MARKETPLACE,
    },
    # NOTE ON TIERS: EMI/Scheme and DEMI are not a simple "more than Enhanced" step;
    # DEMI is a distinct licence focused on issuing e-money and running agents. We model
    # DEMI as building on PSP_ENHANCED capabilities for engineering simplicity (a DEMI in
    # practice also aggregates and processes), then adding the e-money set. If the business
    # takes DEMI WITHOUT first holding Enhanced, adjust TIER_ORDER accordingly. [VERIFY]
    Licence.EMI: set(),  # reserved for the PSP (Scheme) card-scheme/switching branch
    Licence.DEMI: {
        Capability.HOLD_CUSTOMER_FUNDS,
        Capability.WALLET_P2P,
        Capability.CASH_IN_OUT,
        Capability.AGENT_NETWORK,
        Capability.WALLET_BANK_TRANSFER,
        Capability.INBOUND_REMITTANCE_TERMINATION,
        Capability.EMBEDDED_FINANCIAL_PRODUCTS,
    },
}

# The order in which tiers accumulate capabilities. A licence grants its own set plus
# everything from the tiers before it in this list.
TIER_ORDER: list[Licence] = [
    Licence.PSP_STANDARD,
    Licence.PSP_MEDIUM,
    Licence.PSP_ENHANCED,
    Licence.DEMI,
]


def capabilities_for(licence: Licence) -> frozenset[Capability]:
    """Return the full, effective set of capabilities permitted by `licence`."""
    effective: set[Capability] = set()
    for tier in TIER_ORDER:
        effective |= _GRANTS.get(tier, set())
        if tier == licence:
            break
    else:
        # Licence not in the cumulative ladder (e.g. EMI scheme branch): just its own grant.
        effective = set(_GRANTS.get(licence, set()))
    return frozenset(effective)
