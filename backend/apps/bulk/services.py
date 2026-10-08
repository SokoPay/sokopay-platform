"""
Bulk payout services: the only place batch state and batch money change.

LICENCE: BULK_DISBURSEMENT (PSP Medium — paying out of merchant balances through the
partner's rails). Items that land in SokoPay wallets additionally need
HOLD_CUSTOMER_FUNDS (DEMI); without it those rows are marked invalid at upload.

Money flow (all in the merchant's currency, integer pesewas):
  approve:   Dr merchant_payable:<m>  (amount + fees of every ready row)
             Cr bulk_in_flight
  per item — SokoPay wallet:  Dr bulk_in_flight (amount)        Cr customer_wallet:<u>
           — external, paid:  Dr bulk_in_flight (amount + fee)  Cr partner_clearing:<route> (amount)
                                                                Cr fee_revenue (fee)
           — external, failed: Dr bulk_in_flight (amount + fee) Cr merchant_payable:<m>   (refund)
Reserving the whole batch at approval means a concurrent settlement can't spend the
same money, and every pesewa is always in exactly one place.

Maker-checker: the person who uploaded the batch may not approve it, unless they are
the merchant's only Owner/Finance user — then the approval is allowed and flagged
`self_approved` so SokoPay's risk team can see it.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.money import Money
from apps.connectors import registry
from apps.connectors.placeholder import ConnectorNotImplemented
from apps.connectors.types import (
    ConnectorResult,
    DestinationType,
    TransferDestination,
    TransferRequest,
)
from apps.kyc import limits as kyc_limits
from apps.kyc.exceptions import KycError
from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import is_enabled, require_capability
from apps.merchants.models import Merchant, MerchantMember
from apps.notifications.services import ghs, notify
from apps.rails.types import RailStatus
from apps.common.audit import audited

from . import parser, validate
from .exceptions import BulkError
from .models import BulkPayout, BulkPayoutItem

logger = logging.getLogger("sokopay.bulk")

UPLOAD_ROLES = ("owner", "admin", "finance")
APPROVE_ROLES = ("owner", "finance")
DUPLICATE_WINDOW_DAYS = 7


# ---------------------------------------------------------------------------
# Create / review
# ---------------------------------------------------------------------------
def create_batch(*, merchant: Merchant, created_by, filename: str, data: bytes,
                 note: str = "") -> BulkPayout:
    """Parse + validate an uploaded file into a DRAFT batch. No money moves."""
    require_capability(Capability.BULK_DISBURSEMENT)
    if not merchant.is_live:
        raise BulkError("Bulk payments are available once your business is approved.")
    _require_role(merchant, created_by, UPLOAD_ROLES, "upload a bulk payment")

    digest = hashlib.sha256(data).hexdigest()
    recent_dupe = BulkPayout.objects.filter(
        merchant=merchant, file_sha256=digest,
        created_at__gte=timezone.now() - timedelta(days=DUPLICATE_WINDOW_DAYS),
    ).exclude(status__in=(BulkPayout.Status.REJECTED, BulkPayout.Status.CANCELLED)).first()
    if recent_dupe:
        raise BulkError(
            f"This exact file was already uploaded on "
            f"{timezone.localtime(recent_dupe.created_at):%d %b %H:%M} "
            f"({recent_dupe.get_status_display()}). Cancel that batch first if you meant to redo it."
        )

    rows = parser.parse(filename, data)          # raises ParseError (a BulkError)
    items = validate.validate_rows(rows)

    with transaction.atomic():
        batch = BulkPayout.objects.create(
            merchant=merchant, created_by=created_by, note=note.strip()[:120],
            source_filename=(filename or "upload")[:200], file_sha256=digest,
        )
        for item in items:
            item.batch = batch
        BulkPayoutItem.objects.bulk_create(items, batch_size=500)
        _recount(batch)
    return batch


def submit(batch: BulkPayout, *, by, exclude_invalid: bool = False) -> BulkPayout:
    """Maker step: send a reviewed draft for approval."""
    _require_role(batch.merchant, by, UPLOAD_ROLES, "submit a bulk payment")
    with transaction.atomic():
        batch = BulkPayout.objects.select_for_update().get(pk=batch.pk)
        if batch.status != BulkPayout.Status.DRAFT:
            raise BulkError("This batch has already been submitted.")
        if batch.invalid_count and not exclude_invalid:
            raise BulkError(
                f"{batch.invalid_count} row(s) have problems. Fix the file and upload again, "
                "or tick 'skip invalid rows' to pay only the valid ones."
            )
        if batch.invalid_count:
            batch.items.filter(status=BulkPayoutItem.Status.INVALID).update(
                status=BulkPayoutItem.Status.SKIPPED, updated_at=timezone.now())
            batch.exclude_invalid = True
        _recount(batch)
        if batch.valid_count == 0:
            raise BulkError("There are no valid rows to pay.")
        from apps.merchants.settlement import available_balance_minor   # less disputed amounts on hold
        available = available_balance_minor(batch.merchant)
        if batch.needed_minor > available:
            raise BulkError(
                f"This batch needs {ghs(batch.needed_minor)} (including fees) but only "
                f"{ghs(available)} is available."
            )
        batch.status = BulkPayout.Status.AWAITING_APPROVAL
        batch.save(update_fields=["status", "exclude_invalid", "updated_at"])
    return batch


def cancel(batch: BulkPayout, *, by) -> BulkPayout:
    _require_role(batch.merchant, by, UPLOAD_ROLES, "cancel a bulk payment")
    with transaction.atomic():
        batch = BulkPayout.objects.select_for_update().get(pk=batch.pk)
        if batch.status not in (BulkPayout.Status.DRAFT, BulkPayout.Status.AWAITING_APPROVAL):
            raise BulkError("Only a draft or an unapproved batch can be cancelled.")
        batch.status = BulkPayout.Status.CANCELLED
        batch.decided_at = timezone.now()
        batch.save(update_fields=["status", "decided_at", "updated_at"])
    return batch


# ---------------------------------------------------------------------------
# Approve / reject (checker)
# ---------------------------------------------------------------------------
def approvers(merchant: Merchant) -> list:
    return list(MerchantMember.objects.filter(
        merchant=merchant, role__in=APPROVE_ROLES).select_related("user"))


def can_approve(batch: BulkPayout, user) -> tuple[bool, bool]:
    """(allowed, would_be_self_approval) for this user on this batch."""
    members = approvers(batch.merchant)
    if not any(m.user_id == user.id for m in members):
        return False, False
    if user.id != batch.created_by_id:
        return True, False
    others = [m for m in members if m.user_id != user.id]
    return (not others), True


@audited("bulk.approve", actor="checker", obj="batch", fields=("note",))
def approve(batch: BulkPayout, *, checker, note: str = "") -> BulkPayout:
    """
    Checker step: reserve the money and start paying. The reservation is committed
    before any external call so a crash can never pay recipients twice or lose track
    of merchant money.
    """
    require_capability(Capability.BULK_DISBURSEMENT)
    allowed, is_self = can_approve(batch, checker)
    if not allowed:
        if is_self:
            raise BulkError("You uploaded this batch, so another Owner or Finance user must approve it.")
        raise BulkError("Only an Owner or Finance user can approve a bulk payment.")

    with transaction.atomic():
        batch = BulkPayout.objects.select_for_update().get(pk=batch.pk)
        if batch.status != BulkPayout.Status.AWAITING_APPROVAL:
            raise BulkError("This batch is not awaiting approval.")
        _recount(batch)
        needed = batch.needed_minor
        payable = accounts.merchant_payable(str(batch.merchant_id))
        if needed > natural_balance_of(payable):
            raise BulkError(
                f"Insufficient balance: the batch needs {ghs(needed)} including fees.")
        try:
            post_entry(
                f"Bulk payout {batch.id} reserved ({batch.valid_count} recipients)",
                [debit(payable, needed), credit(accounts.bulk_in_flight(), needed)],
                idempotency_key=f"bulk-reserve:{batch.id}",
                reference=("bulk_payout", str(batch.id)),
            )
        except InsufficientFunds as exc:          # lost a race with a settlement
            raise BulkError("Insufficient balance to reserve this batch.") from exc
        batch.reserved_minor = needed
        batch.approved_by = checker
        batch.self_approved = is_self
        batch.decided_at = timezone.now()
        batch.decision_note = note.strip()[:255]
        batch.status = BulkPayout.Status.PROCESSING
        batch.save(update_fields=["reserved_minor", "approved_by", "self_approved", "decided_at",
                                  "decision_note", "status", "updated_at"])
        if is_self:
            logger.warning("Bulk payout %s self-approved by %s (sole approver)", batch.id, checker.id)

        def _enqueue(batch_id=str(batch.id)):
            from .tasks import process_bulk_payout
            process_bulk_payout.delay(batch_id)

        transaction.on_commit(_enqueue, robust=True)   # a queue outage must not fail the committed action
    return batch


@audited("bulk.reject", actor="checker", obj="batch", fields=("reason",))
def reject(batch: BulkPayout, *, checker, reason: str = "") -> BulkPayout:
    allowed, _ = can_approve(batch, checker)
    if not allowed and checker.id != batch.created_by_id:
        raise BulkError("Only an Owner or Finance user can reject a bulk payment.")
    with transaction.atomic():
        batch = BulkPayout.objects.select_for_update().get(pk=batch.pk)
        if batch.status != BulkPayout.Status.AWAITING_APPROVAL:
            raise BulkError("This batch is not awaiting approval.")
        batch.status = BulkPayout.Status.REJECTED
        batch.approved_by = checker
        batch.decided_at = timezone.now()
        batch.decision_note = reason.strip()[:255]
        batch.save(update_fields=["status", "approved_by", "decided_at", "decision_note", "updated_at"])
    return batch


# ---------------------------------------------------------------------------
# Processing (Celery worker)
# ---------------------------------------------------------------------------
def process_batch(batch_id) -> dict:
    """Pay every ready item. Safe to re-run: paid/failed items are skipped."""
    batch = BulkPayout.objects.filter(pk=batch_id).first()
    if batch is None or batch.status != BulkPayout.Status.PROCESSING:
        return {"skipped": True}
    ids = list(batch.items.filter(status=BulkPayoutItem.Status.VALID)
               .values_list("id", flat=True))
    done = 0
    for item_id in ids:
        try:
            process_item(item_id)
            done += 1
        except Exception:  # one bad row must never stop the rest of the payroll
            logger.exception("Bulk item %s failed unexpectedly", item_id)
    _maybe_complete(batch)
    return {"processed": done}


def process_item(item_id) -> BulkPayoutItem:
    # 1. Claim the row (committed), so a retried task can't send it twice.
    with transaction.atomic():
        item = BulkPayoutItem.objects.select_for_update(of=("self",)).select_related("batch", "recipient_user").get(pk=item_id)
        if item.status != BulkPayoutItem.Status.VALID or item.batch.status != BulkPayout.Status.PROCESSING:
            return item
        if item.destination_type == BulkPayoutItem.Destination.SOKOPAY:
            return _pay_sokopay(item)
        item.status = BulkPayoutItem.Status.PROCESSING
        item.save(update_fields=["status", "updated_at"])

    # 2. Call the external institution — outside any DB transaction.
    destination = TransferDestination(DestinationType(item.destination_type), item.institution, item.account)
    try:
        conn = registry.transfer_connector(destination)
        if not conn.available or destination.type not in conn.supports:
            raise ConnectorNotImplemented(f"{conn.key} cannot reach {destination.type}")
        result = conn.send(TransferRequest(
            amount=Money(item.amount_minor, "GHS"),
            destination=destination,
            sender_name=(item.batch.merchant.trading_name or item.batch.merchant.legal_name)[:60],
            reference=f"BULK-{str(item.batch_id)[:8]}-{item.row_number}",
            narrative=(item.narrative or item.batch.note or "Payment from " + item.batch.merchant.legal_name)[:60],
            idempotency_key=f"bulk-item:{item.id}",
        ))
    except ConnectorNotImplemented as exc:
        logger.error("Bulk item %s hit a placeholder connector: %s", item.id, exc)
        result = ConnectorResult(status=RailStatus.FAILED, failure_code="provider_unavailable")
    except Exception:
        logger.exception("Bulk item %s: connector error", item.id)
        result = ConnectorResult(status=RailStatus.FAILED, failure_code="provider_error")

    if result.provider_ref:
        BulkPayoutItem.objects.filter(pk=item.pk).update(provider_ref=result.provider_ref[:64])
    return apply_item_outcome(item, result.status, result.failure_code)


def _pay_sokopay(item: BulkPayoutItem) -> BulkPayoutItem:
    """Credit a SokoPay wallet straight from the reserve (called under the item lock)."""
    recipient = item.recipient_user
    failure = ""
    if not is_enabled(Capability.HOLD_CUSTOMER_FUNDS):
        failure = "Wallet credits are not licensed"
    elif recipient is None or not recipient.is_active:
        failure = "Recipient account is no longer active"
    else:
        try:
            kyc_limits.check_credit(recipient, item.amount_minor)
        except KycError as exc:
            failure = f"Recipient's wallet limit: {exc}"

    if failure:
        return _fail_item(item, failure)

    post_entry(
        f"Bulk payout {item.batch_id} row {item.row_number} → wallet",
        [debit(accounts.bulk_in_flight(), item.amount_minor),
         credit(accounts.customer_wallet(str(recipient.id)), item.amount_minor)],
        idempotency_key=f"bulk-item-paid:{item.id}",
        reference=("bulk_payout_item", str(item.id)),
    )
    item.status = BulkPayoutItem.Status.PAID
    item.connector = "sokopay"
    item.completed_at = timezone.now()
    item.save(update_fields=["status", "connector", "completed_at", "updated_at"])
    merchant = item.batch.merchant
    notify(recipient, kind="wallet", title="Money received",
           body=f"{ghs(item.amount_minor)} from {merchant.trading_name or merchant.legal_name}"
                + (f" — {item.narrative}" if item.narrative else "."),
           data={"type": "wallet", "bulk_item": str(item.id)}, app="customer")
    return item


def apply_item_outcome(item: BulkPayoutItem, status: RailStatus,
                       failure_code: str = "") -> BulkPayoutItem:
    """Settle or refund an external item. Idempotent: only PROCESSING items change."""
    with transaction.atomic():
        item = BulkPayoutItem.objects.select_for_update(of=("self",)).select_related("batch").get(pk=item.pk)
        if item.status != BulkPayoutItem.Status.PROCESSING:
            return item
        if status == RailStatus.SUCCEEDED:
            route = (getattr(settings, "RAIL_PROVIDER", "mock") if item.connector == "rail"
                     else item.connector or "unknown")
            clearing = accounts.partner_clearing(route)
            lines = [debit(accounts.bulk_in_flight(), item.total_minor),
                     credit(clearing, item.amount_minor)]
            if item.fee_minor:
                lines.append(credit(accounts.fee_revenue(), item.fee_minor))
            post_entry(
                f"Bulk payout {item.batch_id} row {item.row_number} → {item.institution}",
                lines, idempotency_key=f"bulk-item-paid:{item.id}",
                reference=("bulk_payout_item", str(item.id)),
                allow_negative={clearing.code},
            )
            item.status = BulkPayoutItem.Status.PAID
            item.completed_at = timezone.now()
            item.save(update_fields=["status", "completed_at", "updated_at"])
        elif status == RailStatus.FAILED:
            _fail_item(item, _failure_text(failure_code))
        else:
            return item                                   # still pending; poller will revisit
    _maybe_complete(item.batch)
    return item


def _fail_item(item: BulkPayoutItem, reason: str) -> BulkPayoutItem:
    """Return this row's money (amount + fee) to the merchant. Caller holds the lock."""
    post_entry(
        f"Bulk payout {item.batch_id} row {item.row_number} refunded",
        [debit(accounts.bulk_in_flight(), item.total_minor),
         credit(accounts.merchant_payable(str(item.batch.merchant_id)), item.total_minor)],
        idempotency_key=f"bulk-item-refund:{item.id}",
        reference=("bulk_payout_item", str(item.id)),
    )
    item.status = BulkPayoutItem.Status.FAILED
    item.error = reason[:255]
    item.completed_at = timezone.now()
    item.save(update_fields=["status", "error", "completed_at", "updated_at"])
    return item


def _failure_text(code: str) -> str:
    return {
        "provider_unavailable": "This destination is not connected yet — money returned",
        "provider_error": "The institution could not be reached — money returned",
        "destination_declined": "The institution declined the payment — money returned",
    }.get(code, "Payment failed — money returned")


def _maybe_complete(batch: BulkPayout) -> None:
    with transaction.atomic():
        batch = BulkPayout.objects.select_for_update().get(pk=batch.pk)
        if batch.status != BulkPayout.Status.PROCESSING:
            return
        open_items = batch.items.filter(
            status__in=(BulkPayoutItem.Status.VALID, BulkPayoutItem.Status.PROCESSING)).exists()
        _recount(batch)
        if open_items:
            return
        batch.status = (BulkPayout.Status.COMPLETED_WITH_FAILURES if batch.failed_count
                        else BulkPayout.Status.COMPLETED)
        batch.completed_at = timezone.now()
        batch.save(update_fields=["status", "completed_at", "updated_at"])
        who = f"{batch.paid_count} of {batch.paid_count + batch.failed_count} paid"
        body = f"{batch.note or batch.source_filename}: {who}."
        if batch.failed_count:
            body += " Failed rows were refunded to your balance."
        for user_id in {batch.created_by_id, batch.approved_by_id} - {None}:
            notify(_user(user_id), kind="payment", title="Bulk payment finished", body=body,
                   data={"type": "bulk_payout", "bulk_payout": str(batch.id)}, app="merchant")


def resolve_pending(limit: int = 500) -> dict:
    """Re-query items still PROCESSING at the institution (Celery poller)."""
    resolved = 0
    pending = BulkPayoutItem.objects.filter(
        status=BulkPayoutItem.Status.PROCESSING).exclude(provider_ref="")[:limit]
    for item in pending:
        try:
            if item.connector == "rail":
                from apps.connectors.transfers import RailTransferConnector
                conn = RailTransferConnector()
            else:
                conn = registry.get_connector("transfer", item.connector)
            status = conn.get_status(item.provider_ref).status
        except (ConnectorNotImplemented, registry.UnknownConnector):
            continue
        if apply_item_outcome(item, status).status != BulkPayoutItem.Status.PROCESSING:
            resolved += 1
    return {"resolved": resolved}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _recount(batch: BulkPayout) -> None:
    rows = batch.items.values("status", "amount_minor", "fee_minor")
    counts = {"valid": 0, "invalid": 0, "paid": 0, "failed": 0}
    amount = fee = 0
    for r in rows:
        s = r["status"]
        if s in ("valid", "processing", "paid", "failed"):
            amount += r["amount_minor"]
            fee += r["fee_minor"]
            if s in ("valid", "processing"):
                counts["valid"] += 1
            else:
                counts[s] += 1
        elif s == "invalid":
            counts["invalid"] += 1
    batch.total_count = batch.items.count()
    batch.valid_count = counts["valid"] + counts["paid"] + counts["failed"]
    batch.invalid_count = counts["invalid"]
    batch.paid_count = counts["paid"]
    batch.failed_count = counts["failed"]
    batch.total_amount_minor = amount
    batch.total_fee_minor = fee
    batch.save(update_fields=["total_count", "valid_count", "invalid_count", "paid_count",
                              "failed_count", "total_amount_minor", "total_fee_minor", "updated_at"])


def _require_role(merchant: Merchant, user, roles: tuple, action: str) -> str:
    member = MerchantMember.objects.filter(merchant=merchant, user=user).first()
    if member is None or member.role not in roles:
        raise BulkError(f"Your role cannot {action}. Ask an Owner, Admin or Finance user.")
    return member.role


def _user(user_id):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(pk=user_id).first()


def merchant_batches(merchant: Merchant, limit: int = 50):
    return (BulkPayout.objects.filter(merchant=merchant)
            .select_related("created_by", "approved_by").order_by("-created_at")[:limit])


def awaiting_my_approval(merchant: Merchant, user) -> int:
    allowed_ids = [m.user_id for m in approvers(merchant)]
    if user.id not in allowed_ids:
        return 0
    qs = BulkPayout.objects.filter(merchant=merchant, status=BulkPayout.Status.AWAITING_APPROVAL)
    if len(allowed_ids) > 1:
        qs = qs.filter(~Q(created_by=user))
    return qs.count()
