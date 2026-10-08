"""
Daily merchant settlement run (T+1).

Sweeps approved merchants with an available balance and a verified default settlement
account, and requests a settlement for each. Large amounts or newly-changed accounts
still go through maker-checker (enforced inside request_settlement). This is a PSP
Medium activity, so the whole run is skipped unless the licence permits it.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.licensing.capabilities import Capability
from apps.licensing.gate import is_enabled

from . import settlement
from .exceptions import SettlementError
from .models import Merchant, SettlementAccount

logger = logging.getLogger("sokopay.settlements")

MIN_SETTLEMENT_MINOR = 1_000  # GH₵10.00 floor


def run() -> dict:
    if not is_enabled(Capability.SETTLEMENT_TO_MERCHANT):
        logger.info("Settlement run skipped: not permitted under the current licence.")
        return {"skipped": "not_licensed"}

    created = 0
    merchants = Merchant.objects.filter(status=Merchant.Status.APPROVED)
    for merchant in merchants.iterator():
        available = settlement.available_balance_minor(merchant)
        if available < MIN_SETTLEMENT_MINOR:
            continue
        destination = merchant.settlement_accounts.filter(
            is_default=True, name_check_status=SettlementAccount.NameCheck.MATCHED
        ).first()
        if destination is None:
            continue
        try:
            settlement.request_settlement(
                merchant=merchant, destination=destination,
                requested_by=merchant.owner, amount_minor=available,
            )
            created += 1
        except SettlementError as exc:
            logger.warning("Settlement skipped for %s: %s", merchant.id, exc)
    return {"settlements_created": created}


@shared_task(name="apps.merchants.tasks.run_daily_settlements")
def run_daily_settlements() -> dict:
    return run()


@shared_task(name="apps.merchants.tasks.poll_settlement_payouts")
def poll_settlement_payouts() -> dict:
    """Re-query payouts still PROCESSING; settle or reverse them; flag stuck ones."""
    return settlement.resolve_processing()


@shared_task(name="apps.merchants.tasks.deliver_webhook", ignore_result=True)
def deliver_webhook(delivery_id: str) -> None:
    from . import webhooks
    webhooks.deliver(delivery_id)


@shared_task(name="apps.merchants.tasks.retry_webhooks")
def retry_webhooks() -> int:
    """Send webhook deliveries whose retry time has come."""
    from . import webhooks
    return webhooks.retry_due()


@shared_task(name="apps.merchants.tasks.poll_refund_payouts")
def poll_refund_payouts() -> dict:
    """Re-query MoMo refunds still PROCESSING; complete or reverse them; flag stuck ones."""
    from . import refunds
    return refunds.resolve_processing()
