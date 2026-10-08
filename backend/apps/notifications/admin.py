from django.contrib import admin

from .models import Device, Notification


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "app", "active", "last_seen_at")
    list_filter = ("platform", "app", "active")
    search_fields = ("user__phone",)
    readonly_fields = ("token",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "pushed_at", "read_at", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__phone", "title")

    def has_change_permission(self, request, obj=None):
        return False
