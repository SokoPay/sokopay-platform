from celery import shared_task

from . import services


@shared_task(name="apps.safeguarding.tasks.daily_safeguarding_check")
def daily_safeguarding_check() -> dict:
    check = services.run_check()
    return {"status": check.status, "surplus_minor": check.surplus_minor}
