"""
Turn parsed rows into payout items, validating every one.

Each row becomes a BulkPayoutItem marked VALID or INVALID with a plain-English error,
so the merchant sees exactly what to fix before any money is committed. Nothing here
moves money; it only decides what *could* be paid.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model

from apps.connectors import registry
from apps.connectors.types import DestinationType, TransferDestination
from apps.kyc import limits as kyc_limits
from apps.kyc.exceptions import KycError
from apps.licensing.capabilities import Capability
from apps.licensing.gate import is_enabled

from . import fees
from .models import BulkPayoutItem

User = get_user_model()

MAX_ITEM_MINOR = 50_000_00   # GH₵50,000 per recipient per batch [VERIFY policy]

DEST_ALIASES = {
    "sokopay": "sokopay", "sokopaywallet": "sokopay", "soko": "sokopay", "wallet_sokopay": "sokopay",
    "momo": "momo", "mobilemoney": "momo", "mobile": "momo", "mm": "momo",
    "bank": "bank", "bankaccount": "bank", "banktransfer": "bank",
    "wallet": "wallet", "otherwallet": "wallet", "fintech": "wallet", "fintechwallet": "wallet",
}
MOMO_NETWORKS = {
    "mtn": "mtn", "mtnmomo": "mtn", "momo": "mtn",
    "telecel": "telecel", "telecelcash": "telecel", "vodafone": "telecel", "vodafonecash": "telecel",
    "at": "at", "atmoney": "at", "airteltigo": "at", "airteltigomoney": "at", "airtel": "at", "tigo": "at",
}
# Fintech wallets we have (or have placeholders for) a route to.
WALLET_PROVIDERS = {"gmoney": "gmoney", "g-money": "gmoney", "zeepay": "zeepay"}

_INJECTION = re.compile(r"^[=+\-@\t\r]")   # spreadsheet formula / CSV-injection prefixes


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def normalise_phone(value: str) -> str | None:
    digits = re.sub(r"[^\d]", "", value or "")
    if digits.startswith("00233"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "233" + digits[1:]
    if len(digits) == 12 and digits.startswith("233"):
        return "+" + digits
    return None


def parse_amount(value: str) -> int | None:
    cleaned = re.sub(r"[^\d.\-]", "", (value or "").replace(",", ""))
    if not cleaned:
        return None
    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        return None
    if dec <= 0 or dec != dec.quantize(Decimal("0.01")):
        return None
    return int(dec * 100)


def validate_rows(rows: list[dict]) -> list[BulkPayoutItem]:
    items: list[BulkPayoutItem] = []
    seen: dict[tuple, int] = {}
    sokopay_allowed = is_enabled(Capability.HOLD_CUSTOMER_FUNDS)

    for row in rows:
        item = BulkPayoutItem(row_number=row["_row"], recipient_name=(row["name"] or "")[:120],
                              narrative=(row["narrative"] or "")[:140])
        errors: list[str] = []

        if not item.recipient_name:
            errors.append("Name is required")
        elif _INJECTION.match(item.recipient_name):
            errors.append("Name must not start with = + - or @")
        if item.narrative and _INJECTION.match(item.narrative):
            errors.append("Note must not start with = + - or @")

        dest = DEST_ALIASES.get(_key(row["destination_type"]) or "momo")
        if dest is None:
            errors.append("Destination type must be sokopay, momo, bank or wallet")
        item.destination_type = dest or ""

        amount = parse_amount(row["amount"])
        if amount is None:
            errors.append("Amount must be a positive number with at most 2 decimals")
        elif amount > MAX_ITEM_MINOR:
            errors.append("Amount is above the GH₵50,000 per-recipient limit")
        else:
            item.amount_minor = amount

        inst_key = _key(row["institution"])
        account = (row["account"] or "").strip()

        if dest in ("sokopay", "momo", "wallet"):
            phone = normalise_phone(account)
            if phone is None:
                errors.append("Account must be a Ghana phone number, e.g. 0241234567")
            item.account = phone or account[:34]

        if dest == "sokopay":
            item.institution = "sokopay"
            if not sokopay_allowed:
                errors.append("Paying into SokoPay wallets needs the e-money (DEMI) licence; "
                              "use momo for now")
            elif item.account.startswith("+"):
                user = User.objects.filter(phone=item.account, is_active=True).first()
                if user is None:
                    errors.append("No SokoPay account for this number")
                else:
                    item.recipient_user = user
                    if amount:
                        try:
                            kyc_limits.check_credit(user, amount)
                        except KycError as exc:
                            errors.append(f"Recipient's wallet limit: {exc}")
        elif dest == "momo":
            network = MOMO_NETWORKS.get(inst_key)
            if network is None:
                errors.append("Network must be mtn, telecel or at")
            item.institution = network or row["institution"][:32]
        elif dest == "wallet":
            provider = WALLET_PROVIDERS.get(inst_key) or WALLET_PROVIDERS.get(row["institution"].lower())
            if provider is None:
                errors.append("Wallet provider must be one of: " + ", ".join(sorted(set(WALLET_PROVIDERS.values()))))
            item.institution = provider or row["institution"][:32]
        elif dest == "bank":
            item.institution = (row["institution"] or "").strip().upper()[:32]
            if not item.institution:
                errors.append("Bank code is required for bank transfers")
            digits = re.sub(r"\D", "", account)
            if not (6 <= len(digits) <= 20):
                errors.append("Bank account number must be 6–20 digits")
            item.account = digits[:34]

        # Can we actually reach this destination right now?
        if dest in ("momo", "bank", "wallet") and not errors:
            conn = registry.transfer_connector(TransferDestination(
                DestinationType(dest), item.institution, item.account))
            if not conn.available or DestinationType(dest) not in conn.supports:
                errors.append(f"Transfers to this {dest} destination are not available yet")
            else:
                item.connector = conn.key

        if dest and not errors:
            dup_key = (dest, item.institution, item.account, item.amount_minor)
            if dup_key in seen:
                errors.append(f"Duplicate of row {seen[dup_key]} (same account and amount)")
            else:
                seen[dup_key] = item.row_number

        if errors:
            item.status = BulkPayoutItem.Status.INVALID
            item.error = "; ".join(errors)[:255]
        else:
            item.status = BulkPayoutItem.Status.VALID
            item.fee_minor = fees.bulk_fee(dest, item.amount_minor)
        items.append(item)
    return items
