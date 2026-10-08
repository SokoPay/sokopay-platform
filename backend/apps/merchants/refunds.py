"""
Merchant refunds: money back to the customer from the merchant's SokoPay balance.

Rules (each one closes a way to abuse refunds):
  * Only to the ORIGINAL payer, by the ORIGINAL method. A refund can never be pointed
    at a different phone or wallet, so it can't be used to move money to a third party.
  * Never more than was paid: refunds (processing + succeeded) are summed under a row
    lock on the payment, so two clicks or two staff can't refund twice.
  * Within REFUND_WINDOW_DAYS of the payment (disputes decided by SokoPay are exempt).
  * Paid from merchant_payable: the ledger refuses to take it below zero, so a merchant
    can't refund money they have already settled out.
  * The merchant fee (MDR) is not returned to the merchant.

Money:
  wallet payment:  Dr merchant_payable  Cr customer_wallet          (instant)
  MoMo payment:    Dr merchant_payable  Cr partner_clearing:<rail>  then rail payout
                   payout FAILED → reversed: Dr clearing  Cr merchant_payable
                   PENDING / UNKNOWN → stays PROCESSING until the webhook or poller.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.common.money import Money
from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.payments.models import Payment
from apps.rails.exceptions import RailError
from apps.rails.registry import get_rail
from apps.rails.types import Network, PayoutRequest, RailResult, RailStatus
from apps.common.audit import audited

from .exceptions import RefundError
from .models import Refund

logger = logging.getLogger("sokopay.refunds")

REFUND_WINDOW_DAYS = 180
STUCK_AFTER = timedelta(hours=24)
STUCK_NO_REF_AFTER = timedelta(minutes=10)


def refunded_minor(payment: Payment) -> int:
    """Refunded or on its way back (failed refunds don't count)."""
    agg = payment.refunds.exclude(status=Refund.Status.FAILED).aggregate(s=Sum("amount_minor"))
    return int(agg["s"] or 0)


def refundable_minor(payment: Payment) -> int:
    if payment.purpose != Payment.Purpose.MERCHANT or payment.status not in (
            Payment.Status.SUCCEEDED, Payment.Status.REFUNDED):
        return 0
    return max(0, payment.amount_minor - refunded_minor(payment))


@audited("refund.create", actor="requested_by", fields=("amount_minor", "reason"))
def create_refund(*, payment: Payment, amount_minor: int | None, reason: str, requested_by,
                  dispute=None) -> Refund:
    """Refund all (amount None) or part of a merchant payment."""
    require_capability(Capability.MERCHANT_AGGREGATION)
    reason = (reason or "").strip()
    if not reason:
        raise RefundError("Give a reason for the refund.")

    with transaction.atomic():
        p = Payment.objects.select_for_update(of=("self",)).select_related("merchant", "user").get(pk=payment.pk)
        if p.purpose != Payment.Purpose.MERCHANT or p.merchant_id is None:
            raise RefundError("Only merchant payments can be refunded here.")
        if p.mode == Payment.Mode.TEST:
            raise RefundError("Test payments can't be refunded.")
        if p.status not in (Payment.Status.SUCCEEDED, Payment.Status.REFUNDED):
            raise RefundError("Only completed payments can be refunded.")
        if dispute is None and p.completed_at and \
                p.completed_at < timezone.now() - timedelta(days=REFUND_WINDOW_DAYS):
            raise RefundError(f"Payments older than {REFUND_WINDOW_DAYS} days can't be refunded. "
                              "Contact SokoPay support.")
        remaining = p.amount_minor - refunded_minor(p)
        amount = remaining if amount_minor is None else int(amount_minor)
        if amount <= 0:
            raise RefundError("This payment has already been fully refunded.")
        if amount > remaining:
            raise RefundError(f"You can refund at most {ghs(remaining)} on this payment.")

        cur = p.currency
        payable = accounts.merchant_payable(str(p.merchant_id), cur)
        shop = p.merchant.trading_name or p.merchant.legal_name

        if p.funding_source == Payment.Source.WALLET:
            if p.user is None or p.user.closed_at or not p.user.is_active:
                raise RefundError("The customer's SokoPay account is closed. Contact SokoPay support.")
            refund = Refund.objects.create(payment=p, merchant=p.merchant, amount_minor=amount, currency=cur,
                                           reason=reason[:255], destination=Refund.Destination.WALLET,
                                           requested_by=requested_by, dispute=dispute)
            try:
                post_entry(f"Merchant refund {p.reference} from {shop}",
                           [debit(payable, amount), credit(accounts.customer_wallet(str(p.user_id), cur), amount)],
                           idempotency_key=f"refund:{refund.id}", reference=("refund", str(refund.id)))
            except InsufficientFunds as exc:
                raise RefundError("Your SokoPay balance is too low to cover this refund.") from exc
            refund.status, refund.completed_at = Refund.Status.SUCCEEDED, timezone.now()
            refund.save(update_fields=["status", "completed_at", "updated_at"])
            _after_success(refund, p)
            return refund

        # Mobile money: reserve from the merchant balance now, pay out after commit.
        try:
            Network(p.network)
        except ValueError:
            raise RefundError("This payment method can't be refunded automatically yet. "
                              "Contact SokoPay support.") from None
        if p.network == Network.CARD.value:
            raise RefundError("Card refunds aren't available yet. Contact SokoPay support.")
        rail_name = p.rail or getattr(settings, "RAIL_PROVIDER", "mock")
        refund = Refund.objects.create(payment=p, merchant=p.merchant, amount_minor=amount, currency=cur,
                                       reason=reason[:255], destination=Refund.Destination.MOMO,
                                       requested_by=requested_by, dispute=dispute, rail=rail_name)
        clearing = accounts.partner_clearing(rail_name, cur)
        try:
            post_entry(f"Merchant refund {p.reference} from {shop}",
                       [debit(payable, amount), credit(clearing, amount)],
                       idempotency_key=f"refund:{refund.id}", reference=("refund", str(refund.id)),
                       allow_negative={clearing.code})
        except InsufficientFunds as exc:
            raise RefundError("Your SokoPay balance is too low to cover this refund.") from exc

    # Outside the transaction: never hold ledger locks during a network call.
    try:
        result = get_rail(refund.rail or None).payout(PayoutRequest(
            amount=Money(refund.amount_minor, refund.currency), network=Network(p.network),
            recipient=p.payer, reference=f"REFUND-{refund.id}",
            narrative=f"Refund from {shop}"[:120], idempotency_key=f"refund-payout:{refund.id}"))
    except RailError as exc:
        logger.error("Refund %s payout not sent: %s", refund.id, exc)
        result = RailResult(RailStatus.FAILED, "", failure_code="not_sent")
    Refund.objects.filter(pk=refund.pk).update(rail_ref=(result.provider_ref or "")[:64])
    return apply_outcome(refund, result.status, result.failure_code)


def apply_outcome(refund: Refund, status: RailStatus, failure_code: str = "") -> Refund:
    """Resolve a PROCESSING MoMo refund. Idempotent."""
    with transaction.atomic():
        r = Refund.objects.select_for_update(of=("self",)).select_related("payment", "merchant").get(pk=refund.pk)
        if r.status != Refund.Status.PROCESSING:
            return r
        if status == RailStatus.SUCCEEDED:
            r.status = Refund.Status.SUCCEEDED
        elif status == RailStatus.FAILED:
            clearing = accounts.partner_clearing(r.rail or getattr(settings, "RAIL_PROVIDER", "mock"), r.currency)
            post_entry(f"Merchant refund {r.payment.reference} payout failed, reversed",
                       [debit(clearing, r.amount_minor),
                        credit(accounts.merchant_payable(str(r.merchant_id), r.currency), r.amount_minor)],
                       idempotency_key=f"refund-reverse:{r.id}", reference=("refund", str(r.id)),
                       allow_negative={clearing.code})
            r.status, r.failure_code = Refund.Status.FAILED, (failure_code or "declined")[:64]
        else:
            return r
        r.completed_at = timezone.now()
        r.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])
        if r.status == Refund.Status.SUCCEEDED:
            _after_success(r, Payment.objects.select_for_update().get(pk=r.payment_id))
        else:
            _tell_merchant_failed(r)
    return r


def _after_success(refund: Refund, payment: Payment) -> None:
    """Mark a fully refunded payment, tell the customer and the merchant team."""
    if refunded_minor(payment) >= payment.amount_minor and payment.status != Payment.Status.REFUNDED:
        payment.status = Payment.Status.REFUNDED
        payment.save(update_fields=["status", "updated_at"])
    shop = refund.merchant.trading_name or refund.merchant.legal_name
    where = "your SokoPay wallet" if refund.destination == Refund.Destination.WALLET else payment.payer_masked
    if payment.user_id:
        notify(payment.user, kind="payment", title="Refund received", app="customer",
               body=f"{shop} refunded {ghs(refund.amount_minor)} to {where}.",
               data={"type": "payment", "reference": payment.reference})
    from .notifications import refund_outcome
    refund_outcome(refund)
    from . import webhooks
    webhooks.emit(refund.merchant, "refund.succeeded", webhooks.refund_payload(refund))


def _tell_merchant_failed(refund: Refund) -> None:
    from . import webhooks
    from .notifications import refund_outcome
    refund_outcome(refund)
    webhooks.emit(refund.merchant, "refund.failed", webhooks.refund_payload(refund))


# --- tracking ---------------------------------------------------------------------------
def refresh(refund: Refund) -> Refund:
    if refund.status != Refund.Status.PROCESSING or not refund.rail_ref:
        return refund
    try:
        st = get_rail(refund.rail or None).get_status(refund.rail_ref)
    except RailError as exc:
        logger.warning("Refund %s status check failed: %s", refund.id, exc)
        return refund
    return apply_outcome(refund, st.status, st.failure_code)


def handle_rail_event(provider_ref: str) -> bool:
    """A verified rail webhook for a refund payout (status re-queried, body not trusted)."""
    if not provider_ref:
        return False
    r = Refund.objects.filter(rail_ref=provider_ref).first()
    if r is None:
        return False
    refresh(r)
    return True


def resolve_processing(limit: int = 200) -> dict:
    resolved = 0
    for r in Refund.objects.filter(status=Refund.Status.PROCESSING).exclude(rail_ref="") \
            .order_by("updated_at")[:limit]:
        if refresh(r).status != Refund.Status.PROCESSING:
            resolved += 1
    stuck = stuck_refunds().count()
    if stuck:
        logger.critical("%d refund payout(s) stuck; ops must investigate", stuck)
    return {"resolved": resolved, "stuck": stuck}


def stuck_refunds():
    now = timezone.now()
    return Refund.objects.filter(status=Refund.Status.PROCESSING).filter(
        Q(rail_ref="", updated_at__lt=now - STUCK_NO_REF_AFTER) | Q(updated_at__lt=now - STUCK_AFTER))
