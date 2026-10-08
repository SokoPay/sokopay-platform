"""
Inbound remittance termination (DEMI): credit international transfers into wallets.

The licensed partner converts currency and settles cedis to SokoPay; its signed webhook
tells us who to credit. We:
  1. verify the signature (the view does this via the partner's connector),
  2. de-duplicate on (partner, partner_ref) — partners retry webhooks,
  3. apply the recipient's KYC limits — if the credit would breach them, we REJECT
     and record it, so the partner returns the money to the sender rather than us
     holding funds we can't credit,
  4. post: Dr partner_clearing:<partner>  Cr customer_wallet:<recipient>.

Recipients are identified by phone. Someone without a SokoPay account gets a basic
(tier 0) wallet created, which is how they're invited to claim the money.
"""

from __future__ import annotations

import logging
import re

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.connectors.types import InboundRemittanceEvent
from apps.kyc import limits as kyc_limits
from apps.kyc.exceptions import KycError
from apps.ledger import accounts
from apps.ledger.services import credit, debit, post_entry
from apps.licensing.capabilities import Capability
from apps.licensing.gate import require_capability
from apps.notifications.services import ghs, notify

from .models import InboundRemittance

User = get_user_model()
logger = logging.getLogger("sokopay.remittance")
PHONE_RE = re.compile(r"^\+233\d{9}$")


def credit_inbound(partner: str, event: InboundRemittanceEvent) -> InboundRemittance:
    require_capability(Capability.INBOUND_REMITTANCE_TERMINATION)

    existing = InboundRemittance.objects.filter(partner=partner,
                                                partner_ref=event.partner_ref).first()
    if existing:
        return existing   # duplicate delivery of the same remittance

    def reject(reason: str) -> InboundRemittance:
        logger.warning("Remittance %s/%s rejected: %s", partner, event.partner_ref, reason)
        return InboundRemittance.objects.create(
            partner=partner, partner_ref=event.partner_ref,
            recipient_phone=event.recipient_phone[:16], amount_minor=event.amount_minor,
            currency=event.currency[:3], sender_name=event.sender_name[:150],
            sender_country=event.sender_country[:2], status=InboundRemittance.Status.REJECTED,
            reason=reason,
        )

    if not event.partner_ref:
        raise ValueError("Remittance has no partner reference.")
    if event.currency != "GHS":
        return reject("Only cedi (GHS) settlement is supported; the partner converts currency.")
    if event.amount_minor <= 0:
        return reject("Amount must be positive.")
    if not PHONE_RE.match(event.recipient_phone):
        return reject("Recipient phone must be a Ghana number (+233…).")

    recipient, _ = User.objects.get_or_create(phone=event.recipient_phone,
                                              defaults={"user_type": "consumer"})
    try:
        kyc_limits.check_credit(recipient, event.amount_minor)
    except KycError as exc:
        return reject(f"Recipient limit: {exc}")

    try:
        with transaction.atomic():
            remittance = InboundRemittance.objects.create(
                partner=partner, partner_ref=event.partner_ref, recipient=recipient,
                recipient_phone=event.recipient_phone, amount_minor=event.amount_minor,
                currency=event.currency, sender_name=event.sender_name[:150],
                sender_country=event.sender_country[:2],
                status=InboundRemittance.Status.CREDITED,
            )
            post_entry(
                f"Remittance from {event.sender_name or 'abroad'} ({partner} {event.partner_ref})",
                [debit(accounts.partner_clearing(partner), event.amount_minor),
                 credit(accounts.customer_wallet(str(recipient.id)), event.amount_minor)],
                idempotency_key=f"remit:{partner}:{event.partner_ref}",
                reference=("remittance", str(remittance.id)),
            )
    except IntegrityError:
        # Two deliveries of the same remittance raced; the other one won.
        return InboundRemittance.objects.get(partner=partner, partner_ref=event.partner_ref)
    sender = event.sender_name or "abroad"
    notify(recipient, kind="wallet", title="Money received from abroad",
           body=f"{ghs(event.amount_minor)} from {sender} is in your wallet.",
           data={"type": "wallet"}, app="customer")
    return remittance
