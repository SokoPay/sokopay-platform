"""
Bulk payout pricing (charged to the merchant, per recipient).

  * SokoPay wallet → free (money stays inside SokoPay).
  * MoMo / bank / other wallet → 0.75%, minimum GH₵0.50 (PRD launch pricing).
[VERIFY against the Enhanced PSP's disbursement cost before go-live.]
"""

BULK_FEE_BP_DEFAULT = 75          # 0.75%
BULK_FEE_MIN_MINOR_DEFAULT = 50   # GH₵0.50


def bulk_fee(destination_type: str, amount_minor: int) -> int:
    if destination_type == "sokopay" or amount_minor <= 0:
        return 0
    # Configurable via apps.pricing (bulk_bank / bulk_momo); defaults to BULK_FEE_BP /
    # BULK_FEE_MIN_MINOR above until a price rule is approved.
    from apps.pricing.services import fee
    return fee("bulk_bank" if destination_type == "bank" else "bulk_momo", amount_minor)
