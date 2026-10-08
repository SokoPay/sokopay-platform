# Make the Celery app available when Django starts, so shared_task works everywhere.
from .celery import app as celery_app

__all__ = ("celery_app",)
