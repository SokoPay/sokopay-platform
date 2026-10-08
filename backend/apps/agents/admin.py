from django.contrib import admin

from .models import Agent, AgentTxn


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("display_name", "status", "location", "created_at")
    list_filter = ("status",)
    search_fields = ("display_name", "user__phone")


@admin.register(AgentTxn)
class AgentTxnAdmin(admin.ModelAdmin):
    list_display = ("agent", "kind", "customer_phone", "amount_minor", "created_at")
    list_filter = ("kind",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
