"""Celery tasks for fraud & AML monitoring."""

from celery import shared_task

from . import monitoring, screening


@shared_task(name="apps.compliance.tasks.evaluate_entry", bind=True, max_retries=3, default_retry_delay=30)
def evaluate_entry(self, entry_id: str) -> int:
    try:
        return monitoring.evaluate_entry(entry_id)
    except Exception as exc:     # DB blip — retry; monitoring must not silently skip money
        raise self.retry(exc=exc)


@shared_task(name="apps.compliance.tasks.daily_monitoring")
def daily_monitoring() -> dict:
    return monitoring.run_daily()


@shared_task(name="apps.compliance.tasks.rescreen_all")
def rescreen_all() -> dict:
    return screening.rescreen_all()
