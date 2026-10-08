from celery import shared_task

from . import services
from .models import Notification


@shared_task(name="apps.notifications.tasks.push_notification", max_retries=3,
             default_retry_delay=30)
def push_notification(notification_id: str) -> int:
    note = Notification.objects.filter(pk=notification_id).first()
    if note is None or note.pushed_at is not None:
        return 0
    return services.deliver(note)
