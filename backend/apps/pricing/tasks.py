from celery import shared_task


@shared_task(name="apps.pricing.tasks.pay_agent_commissions")
def pay_agent_commissions() -> dict:
    """Monthly: move accrued agent commission into each active agent's float."""
    from .services import pay_commissions
    return pay_commissions()
