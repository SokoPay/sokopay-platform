"""
Fee calculation.

Kept as a small, pure, well-tested function so pricing is in one auditable place and
never spread through the request handlers (a problem in the old codebase, which also
calculated 'flat' fees as percentages).

Phase 1 pricing is deliberately simple. Real merchant pricing (per-merchant MDR with
caps) arrives with the merchants app and a FeePlan model; this function is the seam
where that plugs in.
"""

from __future__ import annotations

# Flat convenience fee on consumer bill/airtime payments, in minor units (pesewas).
# TODO(pricing): confirm against the Enhanced PSP's biller commission share. [VERIFY]
BILL_FLAT_FEE_MINOR = 50       # GH₵0.50
AIRTIME_FLAT_FEE_MINOR = 0     # airtime/data carried at no customer fee for acquisition


def compute(purpose: str, amount_minor: int) -> int:
    """
    Return the SokoPay fee (minor units) the payer is charged on top of `amount_minor`.

    `purpose` is one of the Payment.Purpose values.
    """
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")

    if purpose in ("bill", "airtime", "data"):
        # Configurable, maker-checker approved prices (apps.pricing); the constants above
        # are the launch defaults used until a rule is approved.
        from apps.pricing.services import fee
        return fee(purpose, amount_minor)
    # Merchant checkout fees are per-merchant (MDR); see merchant_fee().
    return 0


def merchant_fee(amount_minor: int, mdr_bp: int, cap_minor: int) -> int:
    """
    Merchant Discount Rate fee, deducted from the merchant (not added to the payer).

    `mdr_bp` is basis points (150 bp = 1.5%). `cap_minor` caps the fee. The fee is
    rounded half-up to the nearest minor unit and never exceeds the payment amount.
    """
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")
    if mdr_bp < 0 or cap_minor < 0:
        raise ValueError("mdr_bp and cap_minor must be non-negative")
    # Half-up integer rounding: (amount * bp + 5000) // 10000.
    fee = (amount_minor * mdr_bp + 5000) // 10000
    if cap_minor:
        fee = min(fee, cap_minor)
    return min(fee, amount_minor)
