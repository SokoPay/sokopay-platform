from django.contrib import admin

from .models import SecurityEvent


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    """Read-only audit trail of portal security events."""

    list_display = ("created_at", "kind", "user", "actor", "ip")
    list_filter = ("kind",)
    search_fields = ("user__phone", "actor__phone", "ip")
    readonly_fields = [f.name for f in SecurityEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
