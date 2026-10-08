"""
Celery application for background and scheduled work.

Workers run the money-ops jobs that must happen off the request path and on a schedule:
reconciliation, ledger-integrity checks, polling pending payments, and daily
settlement runs. The broker is Redis (ElastiCache in production); task code lives in
each app's tasks.py and is auto-discovered.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("sokopay")
# All Celery settings live in Django settings under the CELERY_ namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
