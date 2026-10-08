"""Tests for the Money value type. These need no database."""

from decimal import Decimal

import pytest

from apps.common.money import Money, MoneyError


def test_from_major_converts_to_minor_units():
    assert Money.from_major("12.50", "GHS").minor == 1250
    assert Money.from_major("0.01", "GHS").minor == 1
    assert Money.from_major(Decimal("100"), "GHS").minor == 10_000


def test_rejects_float_to_avoid_rounding_error():
    with pytest.raises(MoneyError):
        Money.from_major(12.50, "GHS")  # a float — refused on purpose
    with pytest.raises(MoneyError):
        Money(12.5, "GHS")  # minor must be a plain int


def test_rounding_is_half_up():
    # 12.345 -> 12.35 (pesewa), not 12.34
    assert Money.from_major("12.345", "GHS").minor == 1235


def test_formatting_and_major():
    m = Money(1250, "GHS")
    assert str(m) == "GH₵ 12.50"
    assert m.major == Decimal("12.50")
    assert Money(-500, "GHS").format() == "GH₵ -5.00"


def test_arithmetic_same_currency_only():
    assert (Money(1000, "GHS") + Money(500, "GHS")).minor == 1500
    assert (Money(1000, "GHS") - Money(500, "GHS")).minor == 500
    with pytest.raises(MoneyError):
        Money(1000, "GHS") + Money(500, "USD")


def test_unsupported_currency_rejected():
    with pytest.raises(MoneyError):
        Money(100, "XXX")
