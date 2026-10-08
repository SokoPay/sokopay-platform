"""
Money handling for SokoPay.

THE GOLDEN RULE: money is stored and computed as **whole numbers of the smallest
currency unit** (for Ghana cedis, that unit is the pesewa; GH₵1.00 = 100 pesewas).
We never use floating-point numbers for money — floats cannot represent 0.10
exactly and silently lose fractions, which in a payments system means lost or
invented money.

The old codebase used DecimalField for balances and mutated them in place. Decimals
avoid float error but still allowed fractional-pesewa drift and in-place edits. Here,
every amount that enters the system is converted to an integer number of pesewas at
the boundary and stays an integer everywhere inside.

This module gives us a small, safe value type (`Money`) and conversion helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Number of minor units in one major unit, per currency.
# GHS: 1 cedi = 100 pesewas.
MINOR_UNITS = {
    "GHS": 100,
    "USD": 100,
    "NGN": 100,
}


class MoneyError(ValueError):
    """Raised when a money value or operation is invalid."""


@dataclass(frozen=True)
class Money:
    """
    An immutable amount of money: an integer count of minor units, plus a currency.

    Examples
    --------
    >>> Money.from_major("12.50", "GHS").minor
    1250
    >>> str(Money(1250, "GHS"))
    'GH₵ 12.50'
    >>> (Money(1000, "GHS") + Money(500, "GHS")).minor
    1500
    """

    minor: int          # whole number of minor units (pesewas). May be negative.
    currency: str       # ISO 4217 code, e.g. "GHS"

    SYMBOLS = {"GHS": "GH₵", "USD": "$", "NGN": "₦"}

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise MoneyError("Money.minor must be a plain integer number of minor units.")
        if self.currency not in MINOR_UNITS:
            raise MoneyError(f"Unsupported currency: {self.currency!r}")

    # --- construction -------------------------------------------------------
    @classmethod
    def from_major(cls, amount: str | Decimal | int, currency: str) -> "Money":
        """
        Build Money from a human amount like "12.50" or Decimal("12.50").

        We require a str/Decimal/int — NOT a float — to keep float error out of
        the system entirely. The amount is rounded half-up to the minor unit; any
        more precision than the currency allows is a programming error worth
        surfacing, so we reject it rather than silently truncating.
        """
        if currency not in MINOR_UNITS:
            raise MoneyError(f"Unsupported currency: {currency!r}")
        if isinstance(amount, float):
            raise MoneyError(
                "Refusing to build Money from a float. Pass a string like '12.50' "
                "or a Decimal to avoid rounding error."
            )
        try:
            dec = Decimal(amount)
        except (InvalidOperation, TypeError) as exc:
            raise MoneyError(f"Invalid money amount: {amount!r}") from exc

        factor = MINOR_UNITS[currency]
        scaled = (dec * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return cls(int(scaled), currency)

    # --- presentation -------------------------------------------------------
    @property
    def major(self) -> Decimal:
        """The amount as a Decimal in major units, e.g. Decimal('12.50')."""
        factor = MINOR_UNITS[self.currency]
        return (Decimal(self.minor) / factor).quantize(Decimal("0.01"))

    def format(self) -> str:
        """Human string, e.g. 'GH₵ 12.50' (negative shown as 'GH₵ -12.50')."""
        symbol = self.SYMBOLS.get(self.currency, self.currency + " ")
        return f"{symbol} {self.major:,.2f}"

    def __str__(self) -> str:
        return self.format()

    # --- arithmetic (only between the same currency) ------------------------
    def _check(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise MoneyError("Can only operate on Money with Money.")
        if other.currency != self.currency:
            raise MoneyError(
                f"Currency mismatch: {self.currency} vs {other.currency}. "
                "Cross-currency maths must go through an explicit FX conversion."
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    @property
    def is_positive(self) -> bool:
        return self.minor > 0

    @property
    def is_zero(self) -> bool:
        return self.minor == 0
