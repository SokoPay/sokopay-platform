"""
Identify a SokoPay customer from what they tell an agent or a sender: their phone
number (0244058519, +233244058519, 233244058519) or their wallet ID (7XXXXXXXXX).

Never creates an account — money is only ever sent to someone who already exists.
"""

from __future__ import annotations

import re
import secrets

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from .models import WalletNumber

User = get_user_model()
WALLET_PREFIX = "7"


def luhn_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:               # double every second digit from the right (check slot excluded)
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def is_valid_wallet_number(number: str) -> bool:
    return (len(number) == 10 and number.isdigit() and number.startswith(WALLET_PREFIX)
            and luhn_check_digit(number[:-1]) == number[-1])


def wallet_number_for(user) -> str:
    """The user's wallet ID, assigning one the first time it is needed."""
    existing = WalletNumber.objects.filter(user=user).values_list("number", flat=True).first()
    if existing:
        return existing
    for _ in range(20):
        body = WALLET_PREFIX + "".join(secrets.choice("0123456789") for _ in range(8))
        number = body + luhn_check_digit(body)
        try:
            with transaction.atomic():
                return WalletNumber.objects.create(user=user, number=number).number
        except IntegrityError:
            existing = WalletNumber.objects.filter(user=user).values_list("number", flat=True).first()
            if existing:                     # another request assigned it concurrently
                return existing
    raise RuntimeError("Could not allocate a wallet number.")


def format_wallet_number(number: str) -> str:
    return f"{number[:4]} {number[4:7]} {number[7:]}" if len(number) == 10 else number


def normalise_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00233"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "233" + digits[1:]
    return "+" + digits if len(digits) == 12 and digits.startswith("233") else None


class AccountNotFound(Exception):
    pass


def resolve_account(identifier: str):
    """Phone or wallet ID → active user. Raises AccountNotFound with a helpful message."""
    raw = re.sub(r"[\s-]", "", identifier or "")
    if raw.isdigit() and raw.startswith(WALLET_PREFIX) and len(raw) == 10:
        if not is_valid_wallet_number(raw):
            raise AccountNotFound("That wallet ID isn't valid — check the digits.")
        user = User.objects.filter(wallet_number__number=raw, is_active=True).first()
    else:
        phone = normalise_phone(raw)
        if phone is None:
            raise AccountNotFound("Enter a phone number (e.g. 0244058519) or a 10-digit wallet ID.")
        user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None:
        raise AccountNotFound("No SokoPay account found. Ask the customer to sign up first.")
    return user
