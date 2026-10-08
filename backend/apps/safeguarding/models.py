"""
E-money safeguarding.

Customers' e-money must be fully backed by money SokoPay holds in trust at licensed
banks. These models record the trust accounts, their balances (from bank statements or
a balance API), and each check comparing them with what SokoPay owes customers.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class TrustAccount(TimeStampedModel):
    bank_name = models.CharField(max_length=100)
    account_name = models.CharField(max_length=150)
    account_last4 = models.CharField(max_length=4)        # never store the full number here
    connector = models.CharField(max_length=32, blank=True)  # "" = balances entered manually
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "safeguarding_trust_account"

    def __str__(self) -> str:
        return f"{self.bank_name} ••••{self.account_last4}"


class TrustBalance(TimeStampedModel):
    class Source(models.TextChoices):
        MANUAL = "manual", "Entered from bank statement"
        API = "api", "Bank balance API"

    account = models.ForeignKey(TrustAccount, on_delete=models.PROTECT, related_name="balances")
    balance_minor = models.BigIntegerField()
    as_of = models.DateTimeField()                         # the bank's statement time
    source = models.CharField(max_length=8, choices=Source.choices)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name="+")

    class Meta:
        db_table = "safeguarding_trust_balance"
        indexes = [models.Index(fields=["account", "as_of"])]


class SafeguardingCheck(TimeStampedModel):
    class Status(models.TextChoices):
        OK = "ok", "Fully backed"
        SHORTFALL = "shortfall", "SHORTFALL — e-money exceeds trust funds"
        STALE = "stale", "Trust balance out of date"
        NO_DATA = "no_data", "No trust balance recorded"

    liabilities_minor = models.BigIntegerField()      # e-money owed to customers & agents
    trust_total_minor = models.BigIntegerField(null=True, blank=True)
    surplus_minor = models.BigIntegerField(null=True, blank=True)  # trust − liabilities
    status = models.CharField(max_length=10, choices=Status.choices)
    detail = models.JSONField(default=dict)

    class Meta:
        db_table = "safeguarding_check"

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.status}"
