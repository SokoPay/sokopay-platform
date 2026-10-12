"""
Agents selling to customers, and agents buying float by bank deposit.

  sell(...)              airtime, data or a bill (ECG, water, TV…) paid from the agent's float
                         for a customer who handed over cash. The PIN is checked by the view.
                         On success: an AgentTxn for the history, the agent's commission, and
                         a receipt by SMS and/or email to the details the agent entered.
  submit_deposit(...)    the agent paid cash into a SokoPay bank account: record the deposit
                         (bank, amount, slip reference, date, optional slip photo) for staff
                         to check and approve. Only then is the float credited.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common import secure_files
from apps.common.audit import record as audit
from apps.common.money import Money
from apps.notifications.sms import get_sms_provider
from apps.payments import services as payments
from apps.payments.models import Biller, Payment
from apps.wallet.accounts_lookup import normalise_phone

from .exceptions import AgentError
from .models import AgentTxn, DepositAccount, FloatTopUp

logger = logging.getLogger("sokopay.agents")
DEPOSIT_MAX_AGE_DAYS = 30


def _ghs(minor: int) -> str:
    return Money(int(minor), "GHS").format()


def receipt_contacts(phone: str, email: str) -> tuple[str, str]:
    """Validate where the receipt goes: a Ghana phone number, an email, or both (at least one)."""
    phone, email = (phone or "").strip(), (email or "").strip().lower()
    norm = normalise_phone(phone) if phone else ""
    if phone and not norm:
        raise AgentError("Enter the customer's phone number like 0244058519, or leave it blank.")
    if email:
        try:
            validate_email(email)
        except ValidationError:
            raise AgentError("Enter a valid email address, or leave it blank.") from None
    if not (norm or email):
        raise AgentError("Enter the customer's phone number or email for the receipt.")
    return norm or "", email


def _receipt_text(payment: Payment, agent) -> str:
    when = timezone.localtime(payment.completed_at or payment.created_at).strftime("%d %b %Y %H:%M")
    if payment.purpose == Payment.Purpose.AIRTIME:
        what = f"Airtime {_ghs(payment.amount_minor)} for {payment.account_ref}"
    elif payment.purpose == Payment.Purpose.DATA:
        what = f"Data bundle {payment.product_code} ({_ghs(payment.amount_minor)}) for {payment.account_ref}"
    else:
        what = f"{payment.biller.name} {_ghs(payment.amount_minor)}, account {payment.account_ref}"
    lines = ["SokoPay receipt", what]
    if payment.fee_minor:
        lines.append(f"Fee {_ghs(payment.fee_minor)}, total {_ghs(payment.total_minor)}")
    if payment.delivery_token:
        lines.append(f"Token: {payment.delivery_token}")
    lines.append(f"Ref {payment.reference}, {when}, at {agent.display_name}")
    return "\n".join(lines)


def send_receipt(txn: AgentTxn) -> None:
    """Best effort: a failed SMS or email never undoes a completed payment (it's logged)."""
    text = _receipt_text(txn.payment, txn.agent)
    if txn.receipt_phone:
        result = get_sms_provider().send(txn.receipt_phone, text)
        if not result.success:
            logger.warning("Agent receipt SMS failed for %s", txn.payment.reference)
    if txn.receipt_email:
        try:
            send_mail(f"SokoPay receipt {txn.payment.reference}", text,
                      getattr(settings, "DEFAULT_FROM_EMAIL", None), [txn.receipt_email])
        except Exception:  # noqa: BLE001 - email trouble must not fail the sale
            logger.warning("Agent receipt email failed for %s", txn.payment.reference)


def sell(*, agent, kind: str, receipt_phone: str, receipt_email: str, idempotency_key: str | None = None,
         biller_code: str = "", account: str = "", amount_minor: int = 0, telco: str = "",
         bundle_code: str = "") -> tuple[Payment, AgentTxn | None]:
    """Sell airtime / data / a bill payment from the agent's float. Returns (payment, txn or None)."""
    if agent.status != agent.Status.ACTIVE:
        raise AgentError("Your agent account isn't active.")
    phone, email = receipt_contacts(receipt_phone, receipt_email)
    user = agent.user
    try:
        if kind == "airtime":
            biller = Biller.objects.filter(code=biller_code, category=Biller.Category.AIRTIME, is_active=True).first()
            if biller is None:
                raise AgentError("Choose the customer's network.")
            target = normalise_phone(account)
            if not target:
                raise AgentError("Enter the phone number to top up, like 0244058519.")
            payment = payments.initiate_airtime(user=user, biller=biller, phone=target, amount_minor=amount_minor,
                                                source=Payment.Source.AGENT_FLOAT, idempotency_key=idempotency_key)
        elif kind == "data":
            target = normalise_phone(account)
            if not target:
                raise AgentError("Enter the phone number for the bundle, like 0244058519.")
            payment = payments.initiate_data_bundle(user=user, telco=telco, phone=target, bundle_code=bundle_code,
                                                    source=Payment.Source.AGENT_FLOAT,
                                                    idempotency_key=idempotency_key)
        elif kind == "bill":
            biller = (Biller.objects.filter(code=biller_code, is_active=True)
                      .exclude(category__in=(Biller.Category.AIRTIME, Biller.Category.DATA)).first())
            if biller is None:
                raise AgentError("Choose the bill to pay.")
            if not (account or "").strip():
                raise AgentError("Enter the meter or account number.")
            payment = payments.initiate_bill_payment(user=user, biller=biller, account_ref=account.strip()[:64],
                                                     amount_minor=amount_minor, source=Payment.Source.AGENT_FLOAT,
                                                     idempotency_key=idempotency_key)
        else:
            raise AgentError("Choose airtime, data or a bill.")
    except payments.PaymentError as exc:
        raise AgentError(str(exc)) from exc

    if payment.status != Payment.Status.SUCCEEDED:
        return payment, None
    from apps.pricing.services import accrue_commission
    with transaction.atomic():
        txn, created = AgentTxn.objects.get_or_create(
            payment=payment,
            defaults={"agent": agent, "kind": kind, "amount_minor": payment.total_minor, "currency": payment.currency,
                      "customer_phone": phone or (payment.account_ref if kind != "bill" else ""),
                      "receipt_phone": phone, "receipt_email": email})
        if created:
            accrue_commission(txn=txn, product=kind, on_minor=payment.amount_minor)
            transaction.on_commit(lambda: send_receipt(txn))
    return payment, txn


# --- float by bank deposit --------------------------------------------------------------------
def submit_deposit(*, agent, deposit_account_id, amount_minor: int, reference: str, deposit_date,
                   slip=None) -> FloatTopUp:
    from .services import FLOAT_TOPUP_MAX_MINOR
    if agent.status != agent.Status.ACTIVE:
        raise AgentError("Your agent account isn't active.")
    account = DepositAccount.objects.filter(pk=deposit_account_id, is_active=True).first()
    if account is None:
        raise AgentError("Choose the SokoPay bank account you paid into.")
    if not isinstance(amount_minor, int) or amount_minor <= 0:
        raise AgentError("Enter the amount you deposited.")
    if amount_minor > FLOAT_TOPUP_MAX_MINOR:
        raise AgentError("That's above the per-deposit limit. Contact SokoPay to buy more float.")
    ref = (reference or "").strip()
    if len(ref) < 4:
        raise AgentError("Enter the reference on your deposit slip.")
    today = timezone.localdate()
    if deposit_date is None or deposit_date > today or deposit_date < today - dt.timedelta(days=DEPOSIT_MAX_AGE_DAYS):
        raise AgentError(f"Enter the date on the slip (within the last {DEPOSIT_MAX_AGE_DAYS} days).")
    slip_key = ""
    if slip is not None:
        try:
            slip_key = secure_files.store(f"float-slips/{agent.pk}", slip, secure_files.IMAGES_AND_PDF)
        except secure_files.FileRejected as exc:
            raise AgentError(f"Slip photo: {exc}") from exc
    try:
        with transaction.atomic():
            topup = FloatTopUp.objects.create(
                agent=agent, amount_minor=amount_minor, payment_reference=ref[:64], requested_by=agent.user,
                channel=FloatTopUp.Channel.AGENT, deposit_account=account, deposit_date=deposit_date,
                slip_key=slip_key)
    except IntegrityError:
        raise AgentError("That deposit reference has already been submitted.") from None
    audit("float.deposit_submitted", actor=agent.user, obj=topup, amount_minor=amount_minor, bank=account.bank_name)
    return topup
