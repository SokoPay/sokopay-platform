"""
Interoperable transfers: send from a SokoPay wallet to any MoMo wallet, bank account
or other fintech wallet in Ghana.

LICENCE (DEMI): wallet → wallet on/off-net needs WALLET_P2P; wallet → bank needs
WALLET_BANK_TRANSFER.

Money flow — reserve, send, then settle or refund:
  1. reserve: Dr customer_wallet          Cr interop_in_flight   (committed before sending)
  2. send via the destination's connector (outside any DB transaction)
  3a. success: Dr interop_in_flight       Cr partner_clearing:<route>
  3b. failure: Dr interop_in_flight       Cr customer_wallet      (full refund)
  pending outcomes stay reserved until the poller (tasks.py) resolves them.

Reserving first means a concurrent spend can't use the same money, and a crash between
steps leaves the funds visibly "in flight" rather than lost or double-spent.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.money import Money
from apps.connectors import registry
from apps.connectors.placeholder import ConnectorNotImplemented
from apps.connectors.types import (
    AccountLookup,
    ConnectorResult,
    DestinationType,
    TransferDestination,
    TransferRequest,
)
from apps.kyc import limits as kyc_limits
from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify
from apps.payments.reference import new_reference
from apps.rails.types import RailStatus

from . import services as wallet
from .exceptions import WalletError
from .models import ExternalTransfer

logger = logging.getLogger("sokopay.interop")

_CAPABILITY = {
    DestinationType.MOMO: Capability.WALLET_P2P,
    DestinationType.WALLET: Capability.WALLET_P2P,
    DestinationType.BANK: Capability.WALLET_BANK_TRANSFER,
}


class TransferError(WalletError):
    """An external transfer could not be started."""


def _usable_connector(destination: TransferDestination):
    conn = registry.transfer_connector(destination)
    if not conn.available or destination.type not in conn.supports:
        return None
    return conn


def _connector_by_key(key: str):
    if key == "rail":
        from apps.connectors.transfers import RailTransferConnector
        return RailTransferConnector()
    return registry.get_connector("transfer", key)


def name_enquiry(destination: TransferDestination) -> AccountLookup:
    require_capability(_CAPABILITY[destination.type])
    conn = _usable_connector(destination)
    unavailable = AccountLookup(found=False, supported=False,
                                message="Transfers to this destination are not available yet.")
    if conn is None:
        return unavailable
    try:
        return conn.name_enquiry(destination)
    except ConnectorNotImplemented:
        return unavailable


def send_external(*, sender, destination: TransferDestination, amount_minor: int,
                  narrative: str = "", idempotency_key: str | None = None,
                  currency: str = "GHS") -> ExternalTransfer:
    require_capability(_CAPABILITY[destination.type])
    if amount_minor <= 0:
        raise TransferError("Amount must be positive.")

    if idempotency_key:
        idempotency_key = f"u:{sender.id}:{idempotency_key}"   # scoped per sender
        existing = ExternalTransfer.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    conn = _usable_connector(destination)
    if conn is None:
        raise TransferError("Transfers to this destination are not available yet.")

    # Confirm the recipient exists before touching money.
    try:
        lookup = conn.name_enquiry(destination)
    except ConnectorNotImplemented as exc:
        raise TransferError("Transfers to this destination are not available yet.") from exc
    if lookup.supported and not lookup.found:
        raise TransferError("Recipient account not found.")

    # 1. Reserve (committed before we call out).
    with transaction.atomic():
        if wallet.balance(sender, currency) < amount_minor:
            raise TransferError("Insufficient wallet balance.")
        kyc_limits.check_debit(sender, amount_minor)
        transfer = ExternalTransfer.objects.create(
            reference=new_reference(),
            sender=sender,
            destination_type=str(destination.type),
            institution=destination.institution,
            account=destination.account,
            account_name=lookup.account_name,
            amount_minor=amount_minor,
            currency=currency,
            narrative=narrative[:140],
            connector=conn.key,
            idempotency_key=idempotency_key,
        )
        post_entry(
            f"Transfer out {transfer.reference} (reserved)",
            [debit(accounts.customer_wallet(str(sender.id), currency), amount_minor),
             credit(accounts.interop_in_flight(currency), amount_minor)],
            idempotency_key=f"xfer-reserve:{transfer.id}",
            reference=("transfer", str(transfer.id)),
        )

    # 2. Send.
    try:
        result = conn.send(TransferRequest(
            amount=Money(amount_minor, currency),
            destination=destination,
            sender_name=sender.full_name or sender.phone,
            reference=transfer.reference,
            narrative=narrative or "SokoPay transfer",
            idempotency_key=f"xfer:{transfer.id}",
        ))
    except ConnectorNotImplemented as exc:
        logger.error("Transfer %s hit a placeholder: %s", transfer.reference, exc)
        result = ConnectorResult(status=RailStatus.FAILED, failure_code="provider_unavailable")

    transfer.provider_ref = result.provider_ref
    transfer.save(update_fields=["provider_ref", "updated_at"])

    # 3. Settle, refund, or leave pending.
    return apply_outcome(transfer, result.status, result.failure_code)


def apply_outcome(transfer: ExternalTransfer, status: RailStatus,
                  failure_code: str = "") -> ExternalTransfer:
    """Settle or refund a pending transfer. Idempotent: non-pending transfers are left alone."""
    with transaction.atomic():
        t = ExternalTransfer.objects.select_for_update().get(pk=transfer.pk)
        if t.status != ExternalTransfer.Status.PENDING:
            return t
        in_flight = accounts.interop_in_flight(t.currency)
        if status == RailStatus.SUCCEEDED:
            settle_key = getattr(settings, "RAIL_PROVIDER", "mock") if t.connector == "rail" else t.connector
            clearing = accounts.partner_clearing(settle_key, t.currency)
            post_entry(
                f"Transfer out {t.reference} (settled)",
                [debit(in_flight, t.amount_minor), credit(clearing, t.amount_minor)],
                idempotency_key=f"xfer-settle:{t.id}",
                reference=("transfer", str(t.id)),
                allow_negative={clearing.code},
            )
            t.status = ExternalTransfer.Status.SUCCEEDED
        elif status == RailStatus.FAILED:
            post_entry(
                f"Transfer out {t.reference} (refunded)",
                [debit(in_flight, t.amount_minor),
                 credit(accounts.customer_wallet(str(t.sender_id), t.currency), t.amount_minor)],
                idempotency_key=f"xfer-refund:{t.id}",
                reference=("transfer", str(t.id)),
            )
            t.status = ExternalTransfer.Status.FAILED
            t.failure_code = failure_code or "destination_declined"
        else:
            return t  # still pending
        t.completed_at = timezone.now()
        t.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])

        who = t.account_name or t.account
        if t.status == ExternalTransfer.Status.SUCCEEDED:
            notify(t.sender, kind="transfer", title="Transfer sent",
                   body=f"{ghs(t.amount_minor)} sent to {who}.", app="customer",
                   data={"type": "transfer", "reference": t.reference})
        else:
            notify(t.sender, kind="transfer", title="Transfer returned",
                   body=f"Your {ghs(t.amount_minor)} transfer to {who} didn't go through. "
                        "The money is back in your wallet.", app="customer",
                   data={"type": "transfer", "reference": t.reference})
        return t


def resolve_pending(limit: int = 500) -> dict:
    """Re-query pending transfers and settle/refund them (run by the Celery poller)."""
    resolved = 0
    pending = ExternalTransfer.objects.filter(
        status=ExternalTransfer.Status.PENDING
    ).exclude(provider_ref="")[:limit]
    for t in pending:
        try:
            status = _connector_by_key(t.connector).get_status(t.provider_ref).status
        except ConnectorNotImplemented:
            continue
        before = t.status
        if apply_outcome(t, status).status != before:
            resolved += 1
    return {"resolved": resolved}
