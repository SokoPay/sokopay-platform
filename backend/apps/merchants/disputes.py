"""
Customer disputes on merchant payments.

  customer opens  →  OPEN (merchant has DISPUTE_RESPONSE_DAYS to answer)
  merchant refunds → RESOLVED_CUSTOMER          (refund made through refunds.py)
  merchant explains → RESPONDED → SokoPay staff decide:
        for the customer → refund from the merchant balance → RESOLVED_CUSTOMER
        for the merchant → RESOLVED_MERCHANT
  customer withdraws → WITHDRAWN

Safeguards:
  * Only the payer can dispute (the wallet owner, or the phone that paid by MoMo).
  * One active dispute per payment (DB constraint), at most MAX_OPEN_PER_CUSTOMER at a
    time, within DISPUTE_WINDOW_DAYS, never more than is still refundable.
  * While a dispute is active its amount is HELD from settlement (held_minor), so a
    merchant can't empty their balance before SokoPay decides.
  * Staff can decide without the merchant's answer once the response deadline passes,
    so a merchant can't stall a dispute forever. The decision needs a written note.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.notifications.services import ghs, notify
from apps.payments.models import Payment
from apps.common.audit import audited

from . import refunds
from .exceptions import DisputeError, RefundError
from .models import Dispute

DISPUTE_WINDOW_DAYS = 120
DISPUTE_RESPONSE_DAYS = 7
MAX_OPEN_PER_CUSTOMER = 5
APP_MERCHANT = "merchant"


def held_minor(merchant, currency: str = "GHS") -> int:
    agg = Dispute.objects.filter(merchant=merchant, status__in=Dispute.ACTIVE).aggregate(s=Sum("amount_minor"))
    return int(agg["s"] or 0)


def _owns(payment: Payment, user) -> bool:
    return payment.user_id == user.id or (not payment.user_id and payment.payer and payment.payer == user.phone)


def open_dispute(*, customer, reference: str, reason: str, description: str,
                 amount_minor: int | None = None) -> Dispute:
    if reason not in Dispute.Reason.values:
        raise DisputeError("Choose what went wrong.")
    description = (description or "").strip()
    if len(description) < 10:
        raise DisputeError("Tell us a little more about what happened.")
    p = Payment.objects.select_related("merchant").filter(reference=(reference or "").upper()).first()
    if p is None or not _owns(p, customer) or p.purpose != Payment.Purpose.MERCHANT:
        raise DisputeError("We couldn't find that payment on your account.")
    if p.mode == Payment.Mode.TEST or p.status != Payment.Status.SUCCEEDED:
        raise DisputeError("Only completed payments to a business can be disputed.")
    if p.completed_at and p.completed_at < timezone.now() - timedelta(days=DISPUTE_WINDOW_DAYS):
        raise DisputeError(f"Payments older than {DISPUTE_WINDOW_DAYS} days can't be disputed in the app. "
                           "Contact SokoPay support.")
    if Dispute.objects.filter(customer=customer, status__in=Dispute.ACTIVE).count() >= MAX_OPEN_PER_CUSTOMER:
        raise DisputeError("You have several open disputes. Please wait for those to be resolved.")
    refundable = refunds.refundable_minor(p)
    amount = refundable if amount_minor is None else int(amount_minor)
    if refundable <= 0:
        raise DisputeError("This payment has already been refunded.")
    if not 0 < amount <= refundable:
        raise DisputeError(f"You can dispute up to {ghs(refundable)} on this payment.")
    try:
        with transaction.atomic():
            d = Dispute.objects.create(payment=p, merchant=p.merchant, customer=customer, reason=reason,
                                       description=description[:1000], amount_minor=amount,
                                       respond_by=timezone.now() + timedelta(days=DISPUTE_RESPONSE_DAYS))
    except IntegrityError:
        raise DisputeError("There's already an open dispute for this payment.") from None
    shop = p.merchant.trading_name or p.merchant.legal_name
    notify(customer, title="Dispute opened", app="customer",
           body=f"We've told {shop}. They have {DISPUTE_RESPONSE_DAYS} days to respond.",
           data={"type": "payment", "reference": p.reference})
    from . import webhooks
    webhooks.emit(d.merchant, "dispute.opened", webhooks.dispute_payload(d))
    _tell_team(d, "New dispute", f"A customer disputed {ghs(amount)} on {p.reference}. "
                                 f"Respond within {DISPUTE_RESPONSE_DAYS} days.")
    return d


def withdraw(dispute: Dispute, *, customer) -> Dispute:
    with transaction.atomic():
        d = Dispute.objects.select_for_update().get(pk=dispute.pk)
        if d.customer_id != customer.id:
            raise DisputeError("Not your dispute.")
        if not d.is_active:
            raise DisputeError("This dispute is already closed.")
        d.status = Dispute.Status.WITHDRAWN
        d.save(update_fields=["status", "updated_at"])
    _tell_team(d, "Dispute withdrawn", f"The customer withdrew their dispute on {d.payment.reference}.")
    return d


def _refund_for(d: Dispute, *, by, reason: str, previous_status: str) -> None:
    """
    Pay the disputed amount back. Runs AFTER the dispute row has been claimed and
    committed (never inside a transaction: a MoMo refund calls the rail, and the ledger
    reservation must be committed before money leaves). If the refund is refused, the
    dispute goes back to where it was.
    """
    try:
        refunds.create_refund(payment=d.payment, amount_minor=d.amount_minor, reason=reason,
                              requested_by=by, dispute=d)
    except RefundError as exc:
        Dispute.objects.filter(pk=d.pk).update(status=previous_status, decided_by=None, decided_at=None,
                                               decision_note="")
        raise DisputeError(str(exc)) from exc


@audited("dispute.merchant_respond", actor="by", obj="dispute", fields=("accept",))
def merchant_respond(dispute: Dispute, *, by, accept: bool, response: str = "") -> Dispute:
    """Merchant either refunds (accept) or explains for SokoPay to review."""
    response = (response or "").strip()
    if not accept and len(response) < 10:
        raise DisputeError("Explain your side so SokoPay can review it.")
    with transaction.atomic():
        d = Dispute.objects.select_for_update(of=("self",)).select_related("payment", "merchant").get(pk=dispute.pk)
        if d.status != Dispute.Status.OPEN:
            raise DisputeError("This dispute is no longer waiting for your answer.")
        d.merchant_response, d.responded_by, d.responded_at = response[:1000], by, timezone.now()
        if accept:   # claim it so a second click or a staff decision can't refund again
            d.status, d.decided_at = Dispute.Status.RESOLVED_CUSTOMER, timezone.now()
            d.decision_note = "Refunded by the merchant."
        else:
            d.status = Dispute.Status.RESPONDED
        d.save()
    if accept:
        _refund_for(d, by=by, reason=f"Dispute accepted: {response or d.get_reason_display()}",
                    previous_status=Dispute.Status.OPEN)
    else:
        notify(d.customer, title="Merchant responded", app="customer",
               body="The business has replied to your dispute. SokoPay will review it and let you know.",
               data={"type": "payment", "reference": d.payment.reference})
    return d


@audited("dispute.decide", actor="staff", obj="dispute", fields=("for_customer", "note"))
def decide(dispute: Dispute, *, staff, for_customer: bool, note: str) -> Dispute:
    note = (note or "").strip()
    if len(note) < 10:
        raise DisputeError("Write down the reason for the decision.")
    with transaction.atomic():
        d = Dispute.objects.select_for_update(of=("self",)).select_related("payment", "merchant").get(pk=dispute.pk)
        if not d.is_active:
            raise DisputeError("This dispute is already closed.")
        if d.status == Dispute.Status.OPEN and d.respond_by > timezone.now():
            raise DisputeError("The merchant still has time to respond.")
        previous = d.status
        d.status = Dispute.Status.RESOLVED_CUSTOMER if for_customer else Dispute.Status.RESOLVED_MERCHANT
        d.decided_by, d.decided_at, d.decision_note = staff, timezone.now(), note[:1000]
        d.save()
    if for_customer:
        _refund_for(d, by=staff, reason=f"Dispute decided by SokoPay: {note}", previous_status=previous)
    else:
        notify(d.customer, title="Dispute closed", app="customer",
               body="We reviewed your dispute and found in the business's favour. Contact support if you disagree.",
               data={"type": "payment", "reference": d.payment.reference})
    from . import webhooks
    d.refresh_from_db()
    webhooks.emit(d.merchant, "dispute.resolved", webhooks.dispute_payload(d))
    _tell_team(d, "Dispute decided",
               f"SokoPay decided the dispute on {d.payment.reference} "
               f"{'for the customer (refunded)' if for_customer else 'in your favour'}.")
    return d


def overdue():
    return Dispute.objects.filter(status=Dispute.Status.OPEN, respond_by__lt=timezone.now())


def for_staff_queue():
    return Dispute.objects.filter(Q(status=Dispute.Status.RESPONDED)
                                  | Q(status=Dispute.Status.OPEN, respond_by__lt=timezone.now()))


def _tell_team(d: Dispute, title: str, body: str) -> None:
    from .notifications import MONEY_ROLES, _members
    for user in _members(d.merchant, MONEY_ROLES):
        notify(user, kind="payment", title=title, body=body, app=APP_MERCHANT,
               data={"type": "merchant_payment", "reference": d.payment.reference})
