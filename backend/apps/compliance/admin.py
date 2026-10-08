from django.contrib import admin

from .models import Alert, LargeTransactionReport, ScreeningMatch, SuspiciousTransactionReport, WatchlistImport


class _ReadOnly(admin.ModelAdmin):
    """Compliance records change only through the back-office workflows (audit trail)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Alert)
class AlertAdmin(_ReadOnly):
    list_display = ("created_at", "rule", "severity", "status", "subject_label", "hits")
    list_filter = ("status", "severity", "rule")


admin.site.register(SuspiciousTransactionReport, _ReadOnly)
admin.site.register(LargeTransactionReport, _ReadOnly)
admin.site.register(ScreeningMatch, _ReadOnly)
admin.site.register(WatchlistImport, _ReadOnly)
