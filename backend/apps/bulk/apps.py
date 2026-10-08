from django.apps import AppConfig


class BulkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bulk"
    verbose_name = "Bulk payouts"
