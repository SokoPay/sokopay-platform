from celery import shared_task


@shared_task(name="apps.common.tasks.ops_snapshot")
def ops_snapshot() -> dict:
    """Every 5 minutes: log the operational numbers CloudWatch alarms on."""
    from .health import log_ops_snapshot
    return log_ops_snapshot()
