from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.compliance"
    verbose_name = "Fraud & AML compliance"

    def ready(self):
        # Watch every ledger entry (after commit) for suspicious patterns.
        from apps.ledger import hooks

        from .monitoring import on_ledger_entry
        hooks.register(on_ledger_entry)
