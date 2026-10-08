"""
Merchant settlement: paying a merchant their available balance.

LICENCE: settling funds to a merchant is a PSP **Medium** activity, gated by
SETTLEMENT_TO_MERCHANT.

Maker-checker: a settlement over the approval threshold, or to a destination account
changed within the last 72 hours, must be approved by a DIFFERENT staff user before it
executes. This is a core control of financial systems and a BoG expectation.

Lifecycle: AWAITING_APPROVAL → PROCESSING (money reserved to clearing, payout sent)
→ PAID (partner confirmed) | FAILED (partner refused — money returned to the merchant).
Outcomes arrive by verified webhook (re-queried, never trusted) or the 5-minute poller;
payouts that never resolve are surfaced to ops as "stuck".

Destinations: mobile money (all rails) and bank accounts (only where the configured
rail supports bank payouts — refused up front otherwise).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.money import Money
from apps.ledger import accounts
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.rails.exceptions import RailError
from apps.rails.registry import get_rail
from apps.rails.types import BankPayoutRequest, Network, PayoutRequest, RailResult, RailStatus
from apps.common.audit import audited

from .exceptions import ApprovalError, SettlementError
from .models import Merchant, Settlement, SettlementAccount

logger = logging.getLogger("sokopay.settlements")

# Above this amount, a settlement needs maker-checker approval. [VERIFY policy]
APPROVAL_THRESHOLD_MINOR = 5_000_000      # GH₵50,000.00
# A destination changed within this window also forces approval.
RECENT_DESTINATION_WINDOW = timedelta(hours=72)


def available_balance_minor(merchant: Merchant, currency: str = "GHS") -> int:
    """
    How much the merchant can currently settle: their payable balance, less the
    amounts under an active customer dispute (held until SokoPay decides, so the
    merchant can't empty the balance before a refund is due).

    (A rolling risk reserve would also be subtracted here once reserves are enabled.)
    """
    from .disputes import held_minor
    payable = accounts.merchant_payable(str(merchant.id), currency)
    return max(0, natural_balance_of(payable) - held_minor(merchant, currency))


def _needs_approval(merchant: Merchant, destination: SettlementAccount, amount_minor: int) -> bool:
    if amount_minor > APPROVAL_THRESHOLD_MINOR:
        return True
    changed_recently = destination.updated_at > timezone.now() - RECENT_DESTINATION_WINDOW
    return changed_recently


def request_settlement(*, merchant: Merchant, destination: SettlementAccount,
                       requested_by, amount_minor: int | None = None,
                       currency: str = "GHS") -> Settlement:
    """
    Request a settlement. Executes immediately if no approval is needed; otherwise
    creates it in AWAITING_APPROVAL for a checker to approve.
    """
    require_capability(Capability.SETTLEMENT_TO_MERCHANT)

    if destination.merchant_id != merchant.id:
        raise SettlementError("Settlement account does not belong to this merchant.")
    if destination.name_check_status != SettlementAccount.NameCheck.MATCHED:
        raise SettlementError("Settlement account name has not been verified.")
    check_destination_supported(destination)

    available = available_balance_minor(merchant, currency)
    amount_minor = available if amount_minor is None else amount_minor
    if amount_minor <= 0:
        raise SettlementError("Nothing available to settle.")
    if amount_minor > available:
        raise SettlementError("Requested amount exceeds the available balance.")

    settlement = Settlement.objects.create(
        merchant=merchant,
        destination=destination,
        amount_minor=amount_minor,
        currency=currency,
        requested_by=requested_by,
        status=Settlement.Status.AWAITING_APPROVAL,
    )

    if _needs_approval(merchant, destination, amount_minor):
        return settlement                      # wait for a checker
    return _execute(settlement)


@audited("settlement.approve", actor="checker", obj="settlement")
def approve_settlement(settlement: Settlement, *, checker) -> Settlement:
    """Approve and execute a settlement awaiting approval (checker must differ from maker)."""
    if settlement.status != Settlement.Status.AWAITING_APPROVAL:
        raise ApprovalError("Settlement is not awaiting approval.")
    if checker.id == settlement.requested_by_id:
        raise ApprovalError("The maker cannot approve their own settlement.")
    if getattr(checker, "user_type", None) != "staff":
        raise ApprovalError("Only SokoPay staff can approve a settlement.")

    settlement.approved_by = checker
    settlement.decided_at = timezone.now()
    settlement.save(update_fields=["approved_by", "decided_at", "updated_at"])
    return _execute(settlement)


@audited("settlement.reject", actor="checker", obj="settlement", fields=("reason",))
def reject_settlement(settlement: Settlement, *, checker, reason: str = "") -> Settlement:
    if settlement.status != Settlement.Status.AWAITING_APPROVAL:
        raise ApprovalError("Settlement is not awaiting approval.")
    if checker.id == settlement.requested_by_id:
        raise ApprovalError("The maker cannot decide their own settlement.")
    settlement.approved_by = checker
    settlement.decided_at = timezone.now()
    settlement.status = Settlement.Status.REJECTED
    settlement.save(update_fields=["approved_by", "decided_at", "status", "updated_at"])
    return settlement


def check_destination_supported(destination: SettlementAccount) -> None:
    """Refuse up front (before any money moves) a destination the rail can't pay."""
    if destination.kind == SettlementAccount.Kind.BANK:
        if not get_rail().supports_bank_payout:
            raise SettlementError(
                "Bank settlements aren't available yet — our payment partner hasn't enabled "
                "bank payouts. Add a mobile-money settlement account for now.")
        if not destination.provider.strip():
            raise SettlementError("Bank code is missing on this settlement account.")
    else:
        try:
            Network(destination.provider.lower())
        except ValueError as exc:
            raise SettlementError("Mobile-money network must be mtn, telecel or at.") from exc


def _execute(settlement: Settlement) -> Settlement:
    """
    Move the money, in three steps (the same reserve → send → resolve pattern as
    interop transfers):

      1. Commit: Dr merchant_payable, Cr partner_clearing:<rail>; status PROCESSING.
      2. Call the rail OUTSIDE any DB transaction (no ledger locks held during a
         network call of up to 30 s).
      3. Apply the rail's answer: SUCCEEDED → PAID; definitive FAILED → reverse and
         FAILED; PENDING/UNKNOWN → stay PROCESSING until the webhook or the poller
         (tasks.poll_settlement_payouts) resolves it.

    A crash between 1 and 2 leaves PROCESSING with no rail reference; that is flagged
    as stuck for ops (we do not blindly re-send a payout).
    """
    check_destination_supported(settlement.destination)
    with transaction.atomic():
        settlement = Settlement.objects.select_for_update().get(pk=settlement.pk)
        if settlement.status != Settlement.Status.AWAITING_APPROVAL:
            return settlement                         # already executed / decided
        rail_name = getattr(settings, "RAIL_PROVIDER", "mock")
        cur = settlement.currency
        clearing = accounts.partner_clearing(rail_name, cur)
        post_entry(
            f"Settlement {settlement.id}",
            [debit(accounts.merchant_payable(str(settlement.merchant_id), cur), settlement.amount_minor),
             credit(clearing, settlement.amount_minor)],
            idempotency_key=f"settle:{settlement.id}",
            reference=("settlement", str(settlement.id)),
            allow_negative={clearing.code},   # clearing may dip between partner top-ups
        )
        settlement.rail = rail_name
        settlement.status = Settlement.Status.PROCESSING
        settlement.save(update_fields=["rail", "status", "updated_at"])

    try:
        result = _send_payout(settlement)
    except RailError as exc:
        # Raised by our side before anything was sent (unsupported route, bad network
        # code) — so it is safe to treat as a definitive failure and reverse.
        logger.error("Settlement %s payout not sent: %s", settlement.id, exc)
        result = RailResult(RailStatus.FAILED, "", failure_code="not_sent")

    Settlement.objects.filter(pk=settlement.pk).update(rail_ref=result.provider_ref[:64])
    return apply_payout_outcome(settlement, result.status, result.failure_code)


def _send_payout(settlement: Settlement) -> RailResult:
    rail = get_rail(settlement.rail or None)
    dest = settlement.destination
    common = {
        "amount": Money(settlement.amount_minor, settlement.currency),
        "reference": f"SETTLE-{settlement.id}",
        "narrative": "SokoPay settlement",
        "idempotency_key": f"settle-payout:{settlement.id}",
    }
    if dest.kind == SettlementAccount.Kind.BANK:
        return rail.bank_payout(BankPayoutRequest(
            bank_code=dest.provider.strip(), account_number=dest.account_no,
            account_name=dest.account_name, **common))
    return rail.payout(PayoutRequest(network=Network(dest.provider.lower()),
                                     recipient=dest.account_no, **common))


# ---------------------------------------------------------------------------
# Outcome tracking
# ---------------------------------------------------------------------------
STUCK_NO_REF_AFTER = timedelta(minutes=10)    # sent? we never got a reference back
STUCK_AFTER = timedelta(hours=24)             # partner still hasn't confirmed


def apply_payout_outcome(settlement: Settlement, status: RailStatus,
                         failure_code: str = "") -> Settlement:
    """
    Resolve a PROCESSING settlement. Idempotent: anything not PROCESSING is returned
    untouched, so duplicate webhooks and overlapping polls are harmless.
    """
    with transaction.atomic():
        s = Settlement.objects.select_for_update(of=("self",)).select_related("merchant").get(pk=settlement.pk)
        if s.status != Settlement.Status.PROCESSING:
            return s
        if status == RailStatus.SUCCEEDED:
            s.status = Settlement.Status.PAID
        elif status == RailStatus.FAILED:
            clearing = accounts.partner_clearing(s.rail or getattr(settings, "RAIL_PROVIDER", "mock"),
                                                 s.currency)
            post_entry(
                f"Settlement {s.id} payout failed — reversed",
                [debit(clearing, s.amount_minor),
                 credit(accounts.merchant_payable(str(s.merchant_id), s.currency), s.amount_minor)],
                idempotency_key=f"settle-reverse:{s.id}",
                reference=("settlement", str(s.id)),
                allow_negative={clearing.code},
            )
            s.status = Settlement.Status.FAILED
            s.failure_code = (failure_code or "declined")[:64]
        else:
            return s                                   # still pending; keep waiting
        s.completed_at = timezone.now()
        s.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])
        _tell_merchant(s)
    return s


def _tell_merchant(s: Settlement) -> None:
    from . import webhooks
    from .notifications import settlement_outcome
    settlement_outcome(s)
    webhooks.emit(s.merchant, "settlement.paid" if s.status == Settlement.Status.PAID else "settlement.failed",
                  webhooks.settlement_payload(s))


def refresh_settlement(settlement: Settlement) -> Settlement:
    """Re-query the rail for one PROCESSING settlement and apply the answer."""
    if settlement.status != Settlement.Status.PROCESSING or not settlement.rail_ref:
        return settlement
    try:
        status = get_rail(settlement.rail or None).get_status(settlement.rail_ref)
    except RailError as exc:
        logger.warning("Settlement %s status check failed: %s", settlement.id, exc)
        return settlement
    return apply_payout_outcome(settlement, status.status, status.failure_code)


def handle_rail_event(provider_ref: str) -> bool:
    """
    A verified rail webhook whose reference isn't a payment: is it a settlement payout?
    The webhook body is never trusted — the status is re-queried. Returns True if handled.
    """
    if not provider_ref:
        return False
    s = Settlement.objects.filter(rail_ref=provider_ref).first()
    if s is None:
        return False
    refresh_settlement(s)
    return True


def resolve_processing(limit: int = 200) -> dict:
    """Poller: re-query every PROCESSING settlement; count the stuck ones for ops."""
    resolved = 0
    for s in Settlement.objects.filter(status=Settlement.Status.PROCESSING) \
            .exclude(rail_ref="").order_by("updated_at")[:limit]:
        if refresh_settlement(s).status != Settlement.Status.PROCESSING:
            resolved += 1
    stuck = stuck_settlements().count()
    if stuck:
        logger.critical("%d settlement payout(s) stuck — ops must investigate "
                        "(admin portal → Approvals → Stuck payouts)", stuck)
    return {"resolved": resolved, "stuck": stuck}


def stuck_settlements():
    now = timezone.now()
    return Settlement.objects.filter(status=Settlement.Status.PROCESSING).filter(
        Q(rail_ref="", updated_at__lt=now - STUCK_NO_REF_AFTER)
        | Q(updated_at__lt=now - STUCK_AFTER)
    ).select_related("merchant", "destination").order_by("updated_at")
