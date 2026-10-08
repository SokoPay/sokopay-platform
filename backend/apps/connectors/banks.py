"""
Trust-bank connectors: read the balance of the bank account(s) where customers'
e-money is held in trust.

A DEMI must keep customer funds safeguarded in trust with licensed banks, so that every
cedi of e-money in wallets is backed by a cedi in the bank. The daily safeguarding check
(apps.safeguarding) compares the two. Until a bank's balance API is integrated, ops
records the statement balance manually in the admin portal.
"""

from __future__ import annotations

from .placeholder import PlaceholderTrustBank


class TrustBankApiConnector(PlaceholderTrustBank):
    key = "trust_bank_api"
    display_name = "Trust-account bank balance API (template)"
    regulator = "Bank of Ghana (the custodian bank)"
    go_live = ("Trust-account agreement with a BoG-licensed bank; read-only balance/"
               "statement API (or SFTP statements) and credentials. [VERIFY]")
