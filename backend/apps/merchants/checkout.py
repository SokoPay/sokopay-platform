"""
Merchant checkout: a customer pays a merchant.

LICENCE: merchant aggregation (holding the merchant's collected funds and settling
them) is a PSP **Medium** activity, so these services are gated by MERCHANT_AGGREGATION.
Under PSP Standard they are built but will refuse to run — the licence gate in action.

Money model (aggregation): the customer pays the price (`amount`). SokoPay's fee (the
merchant discount rate, MDR) is DEDUCTED from the merchant, not added to the customer.
So the merchant is owed `amount - fee`, and SokoPay keeps `fee`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.money import Money
from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.payments import fees
from apps.payments.models import Payment
from apps.payments.reference import new_reference
from apps.rails.registry import get_rail
from apps.rails.types import CollectionRequest, Network, RailStatus

from .exceptions import MerchantError
from .models import Merchant


def initiate_merchant_charge(*, merchant: Merchant, amount_minor: int, network: Network,
                             payer: str, narrative: str = "",
                             idempotency_key: str | None = None, source_ref: str = "") -> Payment:
    """Start a collection where the customer pays `merchant` `amount_minor`."""
    require_capability(Capability.MERCHANT_AGGREGATION)

    if not merchant.is_live:
        raise MerchantError("Merchant is not approved to take live payments.")
    if amount_minor <= 0:
        raise MerchantError("Amount must be positive.")

    if idempotency_key:
        idempotency_key = f"m:{merchant.id}:{idempotency_key}"   # scoped per merchant
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    currency = settings.DEFAULT_CURRENCY
    fee = fees.merchant_fee(amount_minor, merchant.mdr_bp, merchant.mdr_cap_minor)

    payment = Payment.objects.create(
        reference=new_reference(),
        purpose=Payment.Purpose.MERCHANT,
        status=Payment.Status.CREATED,
        merchant=merchant,
        amount_minor=amount_minor,          # the price the customer pays
        fee_minor=fee,                      # deducted from the merchant
        total_minor=amount_minor,           # customer is debited the price, no add-on
        currency=currency,
        network=str(network),
        payer=payer,
        rail=getattr(settings, "RAIL_PROVIDER", "mock"),
        idempotency_key=idempotency_key,
        source_ref=source_ref[:48],
    )

    rail = get_rail()
    result = rail.collect(CollectionRequest(
        amount=Money(amount_minor, currency),
        network=Network(network),
        payer=payer,
        reference=payment.reference,
        narrative=(narrative or merchant.trading_name or merchant.legal_name)[:120],
        idempotency_key=f"collect-init:{payment.id}",
    ))
    payment.rail_ref = result.provider_ref
    if result.status == RailStatus.FAILED:
        payment.status = Payment.Status.FAILED
        payment.failure_code = result.failure_code or "rail_rejected"
        payment.completed_at = timezone.now()
    else:
        payment.status = Payment.Status.PENDING
    payment.save(update_fields=["rail_ref", "status", "failure_code", "completed_at", "updated_at"])
    return payment


@transaction.atomic
def confirm_merchant_collection(payment: Payment) -> Payment:
    """
    Record a successful merchant collection in the ledger.

    Called by the payments webhook handler (after signature + re-query) when a
    purpose=MERCHANT payment succeeds. Idempotent and safe to replay.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_terminal:
        return payment

    cur = payment.currency
    clearing = accounts.partner_clearing(payment.rail, cur)
    payable = accounts.merchant_payable(str(payment.merchant_id), cur)
    fee_acc = accounts.fee_revenue(cur)

    net = payment.amount_minor - payment.fee_minor
    lines = [debit(clearing, payment.amount_minor), credit(payable, net)]
    if payment.fee_minor:
        lines.append(credit(fee_acc, payment.fee_minor))

    post_entry(
        f"Merchant collection {payment.reference}", lines,
        idempotency_key=f"collect:{payment.id}",
        reference=("payment", str(payment.id)),
    )

    payment.status = Payment.Status.SUCCEEDED
    payment.completed_at = timezone.now()
    payment.save(update_fields=["status", "completed_at", "updated_at"])

    from .notifications import payment_received
    payment_received(payment)
    from . import webhooks
    webhooks.emit(payment.merchant, "payment.succeeded", webhooks.payment_payload(payment), mode=payment.mode)
    if payment.source_ref:
        from . import hosted
        hosted.on_paid(payment)          # close the checkout session / count the link use
    return payment
