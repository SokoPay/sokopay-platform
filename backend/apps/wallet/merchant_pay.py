"""
Pay a merchant from the SokoPay wallet (scan-to-pay).

LICENCE: the wallet is DEMI (HOLD_CUSTOMER_FUNDS) and holding the merchant's takings
is aggregation (MERCHANT_AGGREGATION). DEMI includes aggregation on our licence ladder,
so in practice this lights up with DEMI.

Money: the customer pays the price; SokoPay keeps the merchant's MDR fee.
    Dr customer_wallet(price)   Cr merchant_payable(price − fee)   Cr fee_revenue(fee)
Completes instantly — the money is already in SokoPay's books.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.kyc import limits as kyc_limits
from apps.ledger import accounts
from apps.ledger.exceptions import InsufficientFunds
from apps.ledger.services import credit, debit, natural_balance_of, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.merchants import qr
from apps.merchants.exceptions import MerchantError
from apps.merchants.models import Merchant, PaymentRequest
from apps.notifications.services import ghs, notify
from apps.payments import fees
from apps.payments.models import Payment
from apps.payments.reference import new_reference

from .exceptions import WalletError


class MerchantPayError(WalletError):
    pass


@kyc_limits.debits_serialized("user")
def pay(*, user, code: str, amount_minor: int | None = None,
        idempotency_key: str | None = None, currency: str = "GHS") -> Payment:
    """
    Pay the merchant identified by `code` (a scanned QR payload, request token or
    short code). For a fixed-amount request the request's amount is used and any
    client-supplied amount is ignored — the customer can't underpay by editing it.
    """
    require_capability(Capability.HOLD_CUSTOMER_FUNDS)
    require_capability(Capability.MERCHANT_AGGREGATION)

    if idempotency_key:
        idempotency_key = f"u:{user.id}:{idempotency_key}"
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    try:
        target = qr.resolve(code)
    except MerchantError as exc:
        raise MerchantPayError(str(exc)) from exc

    request = None
    if target["type"] == "request":
        request = PaymentRequest.objects.select_related("merchant").get(token=target["request_token"])
        if request.status != PaymentRequest.Status.OPEN:
            raise MerchantPayError("This payment request has " + (
                "already been paid." if request.status == PaymentRequest.Status.PAID else "expired."))
        merchant = request.merchant
        if request.amount_minor is not None:
            amount_minor = request.amount_minor
    else:
        merchant = Merchant.objects.get(short_code=target["merchant_code"])

    if not merchant.is_live:
        raise MerchantPayError("This merchant can't take payments right now.")
    if amount_minor is None or amount_minor <= 0:
        raise MerchantPayError("Enter the amount to pay.")
    if merchant.members.filter(user=user).exists():
        raise MerchantPayError("You can't pay your own business from your wallet.")

    fee = fees.merchant_fee(amount_minor, merchant.mdr_bp, merchant.mdr_cap_minor)
    wallet = accounts.customer_wallet(str(user.id), currency)
    if natural_balance_of(wallet) < amount_minor:
        raise MerchantPayError("Insufficient wallet balance.")
    kyc_limits.check_debit(user, amount_minor)

    with transaction.atomic():
        if request is not None:
            # Lock the request so two customers can't both pay the same dynamic QR.
            request = PaymentRequest.objects.select_for_update().get(pk=request.pk)
            if request.status != PaymentRequest.Status.OPEN:
                raise MerchantPayError("This payment request has already been paid.")

        payment = Payment.objects.create(
            reference=new_reference(),
            purpose=Payment.Purpose.MERCHANT,
            status=Payment.Status.PENDING,
            user=user,
            merchant=merchant,
            amount_minor=amount_minor,
            fee_minor=fee,
            total_minor=amount_minor,
            currency=currency,
            funding_source=Payment.Source.WALLET,
            network="wallet",
            payer=user.phone,
            rail=getattr(settings, "RAIL_PROVIDER", "mock"),
            idempotency_key=idempotency_key,
            account_ref=request.description if request else "",
        )
        lines = [debit(wallet, amount_minor),
                 credit(accounts.merchant_payable(str(merchant.id), currency), amount_minor - fee)]
        if fee:
            lines.append(credit(accounts.fee_revenue(currency), fee))
        try:
            post_entry(f"Scan-to-pay {payment.reference} to {merchant.trading_name or merchant.legal_name}",
                       lines, idempotency_key=f"collect:{payment.id}",
                       reference=("payment", str(payment.id)))
        except InsufficientFunds as exc:          # a concurrent spend emptied the wallet
            raise MerchantPayError("Insufficient wallet balance.") from exc

        payment.status = Payment.Status.SUCCEEDED
        payment.completed_at = timezone.now()
        payment.save(update_fields=["status", "completed_at", "updated_at"])

        if request is not None:
            request.status = PaymentRequest.Status.PAID
            request.payment = payment
            request.save(update_fields=["status", "payment", "updated_at"])

        shop = merchant.trading_name or merchant.legal_name
        notify(user, kind="payment", title="Payment successful", app="customer",
               body=f"{ghs(amount_minor)} paid to {shop}.",
               data={"type": "payment", "reference": payment.reference})
        from apps.merchants.notifications import payment_received
        payment_received(payment, note=request.description if request else "")
        from apps.merchants import webhooks
        webhooks.emit(merchant, "payment.succeeded", webhooks.payment_payload(payment))
        from .saved import touch
        touch(user, "merchant", merchant.short_code or "")

    return payment
