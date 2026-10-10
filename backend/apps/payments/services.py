"""
Payment orchestration services.

This module is the ONLY place that drives a payment through its lifecycle. It ties
together three things that must always agree:

  * the licence gate  (are we allowed to do this at all?)
  * the rail          (move money on the network, via the Enhanced PSP partner)
  * the ledger        (record every movement as balanced double-entry)

Design rules enforced here (all lessons from the old codebase):
  * Capability-gated: a bill payment needs BILL_PAYMENT; airtime needs AIRTIME_DATA.
  * Idempotent: initiation dedupes on the caller's key; confirmation is safe to replay,
    so duplicated webhooks cannot double-charge.
  * Webhooks are never trusted on their own — we verify the signature AND re-query the
    rail for the authoritative status before acting.
  * Money only moves through ledger.post_entry().
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.money import Money
from apps.connectors.placeholder import ConnectorNotImplemented
from apps.connectors.registry import biller_connector, telco_connector
from apps.connectors.types import (
    AccountLookup,
    Bundle,
    BundleRequest,
    ConnectorResult,
    TopupRequest,
)
from apps.kyc import limits as kyc_limits
from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.rails.exceptions import WebhookVerificationError
from apps.rails.registry import get_rail
from apps.rails.types import BillRequest, CollectionRequest, Network, RailStatus

from .models import Biller, Payment, RailEvent
from .reference import new_reference

logger = logging.getLogger("sokopay.payments")

# Map a payment purpose to the capability it requires.
_PURPOSE_CAPABILITY = {
    Payment.Purpose.BILL: Capability.BILL_PAYMENT,
    Payment.Purpose.AIRTIME: Capability.AIRTIME_DATA,
    Payment.Purpose.DATA: Capability.AIRTIME_DATA,
}


class PaymentError(Exception):
    """A payment could not be initiated (validation, biller inactive, etc.)."""


# ---------------------------------------------------------------------------
# Initiation
# ---------------------------------------------------------------------------
def initiate_bill_payment(*, user, biller: Biller, account_ref: str, amount_minor: int,
                          network: Network | None = None, payer: str | None = None,
                          source: str = Payment.Source.MOMO,
                          mode: str = Payment.Mode.LIVE,
                          idempotency_key: str | None = None) -> Payment:
    """Start a consumer bill payment (electricity, water, TV, education…)."""
    return _initiate(
        purpose=Payment.Purpose.BILL, user=user, biller=biller, account_ref=account_ref,
        amount_minor=amount_minor, network=network, payer=payer, source=source, mode=mode,
        idempotency_key=idempotency_key,
    )


def initiate_airtime(*, user, biller: Biller, phone: str, amount_minor: int,
                     network: Network | None = None, payer: str | None = None,
                     source: str = Payment.Source.MOMO,
                     mode: str = Payment.Mode.LIVE,
                     idempotency_key: str | None = None) -> Payment:
    """Start an airtime top-up. `phone` is the number being credited; `payer` pays."""
    return _initiate(
        purpose=Payment.Purpose.AIRTIME, user=user, biller=biller, account_ref=phone,
        amount_minor=amount_minor, network=network, payer=payer, source=source, mode=mode,
        idempotency_key=idempotency_key,
    )


def list_bundles(telco: str) -> list[Bundle]:
    """Data bundles on sale for a network (MTN, Telecel, AT)."""
    require_capability(Capability.AIRTIME_DATA)
    connector = telco_connector(telco)
    if not connector.available:
        raise PaymentError("Data bundles are not available on this network yet.")
    return connector.list_bundles()


def initiate_data_bundle(*, user, telco: str, phone: str, bundle_code: str,
                         pay_network: Network | None = None, payer: str | None = None,
                         source: str = Payment.Source.MOMO,
                         mode: str = Payment.Mode.LIVE,
                         idempotency_key: str | None = None) -> Payment:
    """
    Buy a data bundle for `phone` on network `telco`, paid from the wallet or the
    payer's MoMo on `pay_network` (which may be a different network). The price comes
    from the catalogue, never from the client.
    """
    biller = Biller.objects.filter(
        category=Biller.Category.DATA, network=telco, is_active=True
    ).first()
    if biller is None:
        raise PaymentError("Data bundles are not available on this network.")
    bundle = next((b for b in list_bundles(telco) if b.code == bundle_code), None)
    if bundle is None:
        raise PaymentError("Unknown data bundle.")
    return _initiate(
        purpose=Payment.Purpose.DATA, user=user, biller=biller, account_ref=phone,
        amount_minor=bundle.price_minor, network=pay_network, payer=payer, source=source,
        mode=mode, idempotency_key=idempotency_key, product_code=bundle.code,
    )


def lookup_account(biller: Biller, account: str) -> AccountLookup:
    """Show the customer whose meter/smartcard/account this is before they pay."""
    require_capability(Capability.BILL_PAYMENT)
    connector = biller_connector(biller)
    if not connector.available:
        return AccountLookup(found=False, supported=False,
                             message=f"{biller.name} is not available yet.")
    return connector.lookup_account(biller.rail_biller_code, account)


def _delivery_connector(biller: Biller, rail_name: str | None = None):
    if biller.category in (Biller.Category.AIRTIME, Biller.Category.DATA):
        return telco_connector(biller.network, rail_name=rail_name)
    return biller_connector(biller, rail_name=rail_name)


def _initiate(*, purpose, user, biller, account_ref, amount_minor, network, payer,
              mode, idempotency_key, source: str = Payment.Source.MOMO,
              product_code: str = "") -> Payment:
    # 1. Licence gate: refuse outright if this activity isn't permitted by our licence.
    require_capability(_PURPOSE_CAPABILITY[purpose])
    if source == Payment.Source.WALLET:
        require_capability(Capability.HOLD_CUSTOMER_FUNDS)   # paying from e-money is DEMI
    elif not (network and payer):
        raise PaymentError("Choose a mobile money network and number to pay from.")

    # 2. Idempotent initiation: the same key returns the same payment, never a second one.
    #    Keys are scoped to the user so nobody can be handed another customer's payment
    #    by guessing or reusing a key.
    if idempotency_key:
        idempotency_key = f"u:{user.id}:{idempotency_key}"
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # 3. Validate.
    if not biller.is_active:
        raise PaymentError(f"Biller {biller.code} is not active.")
    if not (biller.min_minor <= amount_minor <= biller.max_minor):
        raise PaymentError("Amount is outside this biller's allowed range.")
    # Never take a customer's money for a provider we can't deliver to yet.
    if not _delivery_connector(biller).available:
        raise PaymentError(f"{biller.name} is not available yet.")

    from . import fees
    currency = settings.DEFAULT_CURRENCY
    fee = fees.compute(purpose, amount_minor)
    total = amount_minor + fee

    if source == Payment.Source.WALLET:
        return _pay_from_wallet(
            purpose=purpose, user=user, biller=biller, account_ref=account_ref,
            amount_minor=amount_minor, fee=fee, total=total, currency=currency,
            mode=mode, idempotency_key=idempotency_key, product_code=product_code,
        )

    # 4. Create the record, then ask the rail to collect from the payer.
    payment = Payment.objects.create(
        reference=new_reference(),
        purpose=purpose,
        status=Payment.Status.CREATED,
        user=user,
        biller=biller,
        account_ref=account_ref,
        product_code=product_code,
        amount_minor=amount_minor,
        fee_minor=fee,
        total_minor=total,
        currency=currency,
        network=str(network),
        payer=payer,
        rail=getattr(settings, "RAIL_PROVIDER", "mock"),
        mode=mode,
        idempotency_key=idempotency_key,
    )

    rail = get_rail()
    result = rail.collect(CollectionRequest(
        amount=Money(total, currency),
        network=Network(network),
        payer=payer,
        reference=payment.reference,
        narrative=f"{biller.name} {account_ref}"[:120],
        idempotency_key=f"collect-init:{payment.id}",
    ))

    payment.rail_ref = result.provider_ref
    if result.status == RailStatus.FAILED:
        payment.status = Payment.Status.FAILED
        payment.failure_code = result.failure_code or "rail_rejected"
        payment.completed_at = timezone.now()
    else:
        # PENDING (normal for MoMo) or already SUCCEEDED (rare) — reflect it.
        payment.status = Payment.Status.PENDING
    payment.save(update_fields=["rail_ref", "status", "failure_code", "completed_at", "updated_at"])
    return payment


@kyc_limits.debits_serialized("user")
def _pay_from_wallet(*, purpose, user, biller, account_ref, amount_minor, fee, total,
                     currency, mode, idempotency_key, product_code) -> Payment:
    """
    Pay from the customer's SokoPay wallet (DEMI). No MoMo prompt: the money is already
    held by SokoPay, so the payment is debited and delivered immediately.
    """
    wallet_acc = accounts.customer_wallet(str(user.id), currency)
    if natural_balance_of(wallet_acc) < total:
        raise PaymentError("Insufficient wallet balance.")
    kyc_limits.check_debit(user, total)

    payment = Payment.objects.create(
        reference=new_reference(),
        purpose=purpose,
        status=Payment.Status.PENDING,
        user=user,
        biller=biller,
        account_ref=account_ref,
        product_code=product_code,
        amount_minor=amount_minor,
        fee_minor=fee,
        total_minor=total,
        currency=currency,
        funding_source=Payment.Source.WALLET,
        network="wallet",
        payer=user.phone,
        rail=getattr(settings, "RAIL_PROVIDER", "mock"),   # still delivers via the partner
        mode=mode,
        idempotency_key=idempotency_key,
    )
    try:
        return confirm_collection_and_deliver(payment)
    except InsufficientFunds as exc:
        # A concurrent spend emptied the wallet between our check and the posting.
        fail_payment(payment, "insufficient_funds")
        raise PaymentError("Insufficient wallet balance.") from exc


# ---------------------------------------------------------------------------
# Completion (driven by webhooks, after a re-query)
# ---------------------------------------------------------------------------
@transaction.atomic
def confirm_collection_and_deliver(payment: Payment) -> Payment:
    """
    The collection succeeded: record it in the ledger, then deliver to the biller.

    If the biller fails after we have taken the customer's money, we park the full
    amount as a refund we owe the customer (refund_payable) and mark the payment
    FAILED. A later refund job pays it back — we never silently keep the money.

    Safe to call more than once: a payment already in a terminal state is returned
    unchanged, and each ledger entry carries an idempotency key as a second guard.
    """
    # Re-read under lock to avoid two webhooks racing the same payment.
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_terminal:
        return payment

    cur = payment.currency
    from_wallet = payment.funding_source == Payment.Source.WALLET
    # Where the customer's money came from: the rail (MoMo collection) or their wallet.
    source_acc = (accounts.customer_wallet(str(payment.user_id), cur) if from_wallet
                  else accounts.partner_clearing(payment.rail, cur))
    biller_acc = accounts.biller_payable(payment.biller.code, cur)
    fee_acc = accounts.fee_revenue(cur)

    # 1. Record the collection: we now hold the money, owe the biller `amount`, and
    #    keep `fee`.
    lines = [debit(source_acc, payment.total_minor), credit(biller_acc, payment.amount_minor)]
    if payment.fee_minor:
        lines.append(credit(fee_acc, payment.fee_minor))
    post_entry(
        f"Collection {payment.reference}", lines,
        idempotency_key=f"collect:{payment.id}",
        reference=("payment", str(payment.id)),
    )

    # 2. Deliver to the biller / telco from our prefunded float, via its connector.
    result = _deliver(payment)
    prefund = accounts.partner_prefund(payment.rail, cur)

    if result.status == RailStatus.SUCCEEDED:
        post_entry(
            f"Bill delivery {payment.reference}",
            [debit(biller_acc, payment.amount_minor), credit(prefund, payment.amount_minor)],
            idempotency_key=f"deliver:{payment.id}",
            reference=("payment", str(payment.id)),
            allow_negative={prefund.code},  # float may dip negative between top-ups
        )
        if result.token:
            payment.delivery_token = result.token     # e.g. ECG prepaid token
            payment.save(update_fields=["delivery_token", "updated_at"])
        _mark(payment, Payment.Status.SUCCEEDED)
    else:
        # Biller declined after we took the money. Wallet payments go straight back to
        # the wallet. MoMo payments are parked as a refund owed (the money is still at
        # the rail) for the refund job to pay out.
        reverse = [debit(biller_acc, payment.amount_minor)]
        if payment.fee_minor:
            reverse.append(debit(fee_acc, payment.fee_minor))
        if from_wallet:
            reverse.append(credit(source_acc, payment.total_minor))
            narrative, final = "Refunded to wallet (biller failed)", Payment.Status.REFUNDED
        else:
            reverse.append(credit(accounts.refund_payable(cur), payment.total_minor))
            narrative, final = "Refund pending (biller failed)", Payment.Status.FAILED
        post_entry(
            f"{narrative} {payment.reference}", reverse,
            idempotency_key=f"refundpend:{payment.id}",
            reference=("payment", str(payment.id)),
        )
        payment.failure_code = result.failure_code or "biller_failed"
        _mark(payment, final)

    return payment


def _deliver(payment: Payment) -> ConnectorResult:
    """
    Hand a collected payment to whoever delivers it: a telco for airtime/data, the
    biller's connector otherwise. A placeholder connector here (which initiation should
    have prevented) is treated as a delivery failure, so the customer is refunded
    rather than the webhook erroring with their money held.
    """
    biller = payment.biller
    cur = payment.currency
    idem = f"bill:{payment.id}"
    connector = _delivery_connector(biller, rail_name=payment.rail)
    try:
        if biller.category == Biller.Category.AIRTIME:
            return connector.topup_airtime(TopupRequest(
                network=biller.network, phone=payment.account_ref,
                amount=Money(payment.amount_minor, cur),
                reference=payment.reference, idempotency_key=idem,
            ))
        if biller.category == Biller.Category.DATA:
            bundle = next((b for b in connector.list_bundles()
                           if b.code == payment.product_code), None)
            if bundle is None:
                return ConnectorResult(status=RailStatus.FAILED,
                                       failure_code="bundle_unavailable")
            return connector.buy_bundle(BundleRequest(
                phone=payment.account_ref, bundle=bundle,
                reference=payment.reference, idempotency_key=idem,
            ))
        return connector.pay(BillRequest(
            amount=Money(payment.amount_minor, cur),
            biller_code=biller.rail_biller_code,
            account=payment.account_ref,
            reference=payment.reference,
            idempotency_key=idem,
        ))
    except ConnectorNotImplemented as exc:
        logger.error("Delivery to placeholder connector for %s: %s", payment.reference, exc)
        return ConnectorResult(status=RailStatus.FAILED, failure_code="provider_unavailable",
                               message=str(exc))


@transaction.atomic
def fail_payment(payment: Payment, failure_code: str) -> Payment:
    """Mark a payment failed (e.g. the collection itself was declined at the rail)."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_terminal:
        return payment
    payment.failure_code = failure_code
    _mark(payment, Payment.Status.FAILED)
    if payment.purpose == Payment.Purpose.MERCHANT and payment.merchant_id:
        from apps.merchants import webhooks
        webhooks.emit(payment.merchant, "payment.failed", webhooks.payment_payload(payment), mode=payment.mode)
    return payment


def _mark(payment: Payment, status: str) -> None:
    payment.status = status
    payment.completed_at = timezone.now()
    payment.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])
    _notify_outcome(payment)


def _notify_outcome(payment: Payment) -> None:
    """Tell the paying customer how their bill/airtime/data payment ended."""
    from apps.notifications.services import ghs, notify
    if payment.user_id is None:
        return
    what = payment.biller.name if payment.biller_id else "payment"
    amount = ghs(payment.total_minor)
    if payment.status == Payment.Status.SUCCEEDED:
        body = f"{amount} paid to {what}."
        if payment.delivery_token:
            body += f" Token: {payment.delivery_token}"
        title = "Payment successful"
    elif payment.status == Payment.Status.REFUNDED:
        title, body = "Payment refunded", f"{what} couldn't be completed. {amount} is back in your wallet."
    else:
        title = "Payment failed"
        body = (f"{what} couldn't be completed. If {amount} left your account it will be refunded."
                if payment.failure_code in ("biller_failed", "provider_unavailable")
                else f"Your {amount} payment to {what} did not go through. Nothing was taken.")
    notify(payment.user, kind="payment", title=title, body=body, app="customer",
           data={"type": "payment", "reference": payment.reference, "status": payment.status})


# ---------------------------------------------------------------------------
# Inbound webhooks (the secure callback path)
# ---------------------------------------------------------------------------
def handle_webhook(rail_name: str, headers: dict, body: bytes) -> Payment | None:
    """
    Process one inbound rail callback.

    Steps, in order, none skippable:
      1. Verify the signature. A bad signature is recorded and rejected.
      2. Store the raw event (audit trail — the old code kept none).
      3. Find the payment by the rail's reference.
      4. RE-QUERY the rail for the authoritative status; act on THAT, not the body.
         This defeats forged or replayed callbacks claiming "success".
    """
    rail = get_rail(rail_name)
    # Normalise header keys to lowercase so lookups are predictable.
    headers = {k.lower(): v for k, v in headers.items()}
    event = rail.verify_and_parse_webhook(headers, body)

    RailEvent.objects.create(
        rail=rail_name,
        provider_ref=event.provider_ref,
        status=str(event.status),
        signature_ok=event.signature_ok,
        payload=event.raw,
        payment=Payment.objects.filter(rail_ref=event.provider_ref).first(),
    )

    if not event.signature_ok:
        raise WebhookVerificationError("Webhook signature verification failed.")

    payment = Payment.objects.filter(rail_ref=event.provider_ref).first()
    if payment is None:
        # Not a collection — maybe a merchant settlement payout (re-queried inside).
        from apps.merchants import settlement as settlement_service
        if not settlement_service.handle_rail_event(event.provider_ref):
            from apps.merchants import refunds as refund_service
            refund_service.handle_rail_event(event.provider_ref)
        # Otherwise unknown: the event is stored for investigation; nothing to update.
        return None

    # Authoritative status comes from a fresh query, not the callback body.
    status = rail.get_status(event.provider_ref).status
    if status == RailStatus.SUCCEEDED:
        _on_collection_success(payment)
    elif status == RailStatus.FAILED:
        fail_payment(payment, "rail_declined")
    # PENDING / UNKNOWN: leave the payment pending; a later callback or poll resolves it.
    return Payment.objects.get(pk=payment.pk)


def _on_collection_success(payment: Payment) -> None:
    """Dispatch a successful collection to the right completion path by purpose."""
    # Lazy imports below avoid payments<->merchants/wallet import cycles.
    if payment.purpose == Payment.Purpose.MERCHANT:
        from apps.merchants.checkout import confirm_merchant_collection
        confirm_merchant_collection(payment)
    elif payment.purpose == Payment.Purpose.WALLET_FUND:
        from apps.wallet.services import confirm_funding
        confirm_funding(payment)
    else:
        confirm_collection_and_deliver(payment)
