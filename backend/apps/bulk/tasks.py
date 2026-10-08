"""Celery tasks for bulk payouts: process an approved batch; poll items left pending."""

from celery import shared_task

from . import services


@shared_task(name="apps.bulk.tasks.process_bulk_payout", bind=True, max_retries=3,
             default_retry_delay=30)
def process_bulk_payout(self, batch_id: str) -> dict:
    try:
        return services.process_batch(batch_id)
    except Exception as exc:  # transient DB/broker trouble — retry; items are idempotent
        raise self.retry(exc=exc)


@shared_task(name="apps.bulk.tasks.poll_bulk_items")
def poll_bulk_items() -> dict:
    """Resolve sent items, and restart approved batches whose start was lost (e.g. the
    queue was down at approval). process_batch is safe to re-run: done items are skipped."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import BulkPayout, BulkPayoutItem
    out = services.resolve_pending()
    stale = timezone.now() - timedelta(minutes=10)
    restarted = 0
    for batch_id in (BulkPayout.objects.filter(status=BulkPayout.Status.PROCESSING, updated_at__lt=stale,
                                               items__status=BulkPayoutItem.Status.VALID)
                     .values_list("id", flat=True).distinct()[:20]):
        services.process_batch(batch_id)
        restarted += 1
    return {**(out if isinstance(out, dict) else {"resolved": out}), "restarted_batches": restarted}
