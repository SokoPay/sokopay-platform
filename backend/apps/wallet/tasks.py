"""Poll interop transfers stuck in PENDING and settle or refund them."""

from celery import shared_task

from . import interop


@shared_task(name="apps.wallet.tasks.poll_external_transfers")
def poll_external_transfers() -> dict:
    return interop.resolve_pending()


@shared_task(name="apps.wallet.tasks.poll_cross_border")
def poll_cross_border() -> dict:
    """Re-query cross-border sends still on their way; deliver or refund."""
    from . import cross_border
    return cross_border.resolve_pending()


@shared_task(name="apps.wallet.tasks.poll_lifestyle_orders")
def poll_lifestyle_orders() -> dict:
    from . import lifestyle
    return lifestyle.resolve_pending()
