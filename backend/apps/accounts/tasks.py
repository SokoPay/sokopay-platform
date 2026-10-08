"""Housekeeping for the JWT denylist."""

from celery import shared_task

from . import tokens


@shared_task(name="apps.accounts.tasks.purge_revoked_tokens")
def purge_revoked_tokens() -> dict:
    """Drop denylist rows for tokens that have expired anyway (daily)."""
    return {"purged": tokens.purge_expired()}


@shared_task(name="apps.accounts.tasks.anonymise_closed_accounts")
def anonymise_closed_accounts() -> dict:
    """Monthly: strip personal data from accounts closed longer than the retention period."""
    from . import services
    return {"anonymised": services.anonymise_closed_accounts()}
