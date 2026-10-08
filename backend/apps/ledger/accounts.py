"""
Chart-of-accounts helpers: convenient, consistent ways to fetch or create the
ledger accounts SokoPay uses. Centralising the account codes here keeps naming
consistent and documents what each account means.

Account code conventions
-------------------------
Global (one per rail/currency):
    partner_clearing:<rail>     ASSET      money the rail owes us from collections
    partner_prefund:<rail>      ASSET      float we placed with the rail for payouts/bills
    fee_revenue                 INCOME     SokoPay's fees
    partner_cost:<rail>         EXPENSE    what the rail charges us
    suspense                    LIABILITY  unmatched funds pending investigation

Per party:
    merchant_payable:<id>       LIABILITY  money we owe a merchant
    merchant_reserve:<id>       LIABILITY  risk reserve held from a merchant
    biller_payable:<code>       LIABILITY  money owed to a biller
    customer_wallet:<id>        LIABILITY  e-money we hold for a customer   (DEMI only)
    agent_float:<id>            LIABILITY  e-money float we hold for an agent (DEMI only)
"""

from __future__ import annotations

from django.conf import settings

from .models import AccountType, Currency, LedgerAccount


def _currency(code: str | None = None) -> Currency:
    code = code or settings.DEFAULT_CURRENCY
    obj, _ = Currency.objects.get_or_create(
        code=code, defaults={"name": {"GHS": "Ghana Cedi"}.get(code, code), "minor_units": 100}
    )
    return obj


def get_or_create_account(
    code: str,
    name: str,
    account_type: AccountType,
    *,
    currency: str | None = None,
    owner_type: str = "",
    owner_id: str = "",
) -> LedgerAccount:
    account, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={
            "name": name,
            "account_type": account_type,
            "currency": _currency(currency),
            "owner_type": owner_type,
            "owner_id": owner_id,
        },
    )
    return account


# --- global accounts --------------------------------------------------------
def partner_clearing(rail: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"partner_clearing:{rail}", f"{rail} clearing", AccountType.ASSET, currency=currency
    )


def partner_prefund(rail: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"partner_prefund:{rail}", f"{rail} prefund float", AccountType.ASSET, currency=currency
    )


def partner_cost(rail: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"partner_cost:{rail}", f"{rail} cost", AccountType.EXPENSE, currency=currency
    )


def fee_revenue(currency: str | None = None) -> LedgerAccount:
    return get_or_create_account("fee_revenue", "Fee revenue", AccountType.INCOME, currency=currency)


def suspense(currency: str | None = None) -> LedgerAccount:
    return get_or_create_account("suspense", "Suspense", AccountType.LIABILITY, currency=currency)


def financial_partner_payable(partner_key: str, currency: str | None = None) -> LedgerAccount:
    """Premiums collected from customers' wallets that we owe to an insurer."""
    return get_or_create_account(
        f"financial_partner_payable:{partner_key}", f"Owed to partner {partner_key}",
        AccountType.LIABILITY, currency=currency, owner_type="financial_partner",
        owner_id=partner_key,
    )


def bulk_in_flight(currency: str | None = None) -> LedgerAccount:
    """Merchant money reserved for an approved bulk payout, until each recipient is
    paid (settles to clearing / wallets) or the item fails (returns to the merchant)."""
    return get_or_create_account(
        "bulk_in_flight", "Bulk payouts in flight", AccountType.LIABILITY, currency=currency,
    )


def interop_in_flight(currency: str | None = None) -> LedgerAccount:
    """Money that has left a customer's wallet for another institution but is not
    yet confirmed delivered. Settles to clearing on success, back to the wallet on failure."""
    return get_or_create_account(
        "interop_in_flight", "Interop transfers in flight", AccountType.LIABILITY,
        currency=currency,
    )


def refund_payable(currency: str | None = None) -> LedgerAccount:
    """Money we owe back to customers (e.g. a bill that failed after we collected)."""
    return get_or_create_account(
        "refund_payable", "Customer refunds payable", AccountType.LIABILITY, currency=currency
    )


# --- per-party accounts -----------------------------------------------------
def merchant_payable(merchant_id: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"merchant_payable:{merchant_id}", f"Merchant {merchant_id} payable",
        AccountType.LIABILITY, currency=currency, owner_type="merchant", owner_id=str(merchant_id),
    )


def merchant_reserve(merchant_id: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"merchant_reserve:{merchant_id}", f"Merchant {merchant_id} reserve",
        AccountType.LIABILITY, currency=currency, owner_type="merchant", owner_id=str(merchant_id),
    )


def biller_payable(biller_code: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"biller_payable:{biller_code}", f"Biller {biller_code} payable",
        AccountType.LIABILITY, currency=currency, owner_type="biller", owner_id=biller_code,
    )


# --- DEMI-only accounts (created, but only transacted once DEMI is licensed) ---
def customer_wallet(user_id: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"customer_wallet:{user_id}", f"Customer {user_id} wallet",
        AccountType.LIABILITY, currency=currency, owner_type="customer", owner_id=str(user_id),
    )


def agent_float(agent_id: str, currency: str | None = None) -> LedgerAccount:
    return get_or_create_account(
        f"agent_float:{agent_id}", f"Agent {agent_id} float",
        AccountType.LIABILITY, currency=currency, owner_type="agent", owner_id=str(agent_id),
    )


def agent_commission(agent_id: str, currency: str | None = None) -> LedgerAccount:
    """Commission earned by an agent and not yet paid into their float (DEMI)."""
    return get_or_create_account(
        f"agent_commission:{agent_id}", f"Agent {agent_id} commission payable",
        AccountType.LIABILITY, currency=currency, owner_type="agent", owner_id=str(agent_id),
    )


def commission_expense(currency: str | None = None) -> LedgerAccount:
    return get_or_create_account("commission_expense", "Agent commission expense", AccountType.EXPENSE,
                                 currency=currency)


def cross_border_in_flight(currency: str | None = None) -> LedgerAccount:
    """Cross-border sends reserved from wallets, not yet confirmed by the partner."""
    return get_or_create_account("cross_border_in_flight", "Cross-border transfers in flight",
                                 AccountType.LIABILITY, currency=currency)


def lifestyle_partner_payable(partner_key: str, currency: str | None = None) -> LedgerAccount:
    """Ticket / food sales we owe to the partner (swept by settlement with them)."""
    return get_or_create_account(f"lifestyle_partner_payable:{partner_key}", f"Owed to partner {partner_key}",
                                 AccountType.LIABILITY, currency=currency)


def lifestyle_in_flight(currency: str | None = None) -> LedgerAccount:
    return get_or_create_account("lifestyle_in_flight", "Lifestyle orders in flight", AccountType.LIABILITY,
                                 currency=currency)
