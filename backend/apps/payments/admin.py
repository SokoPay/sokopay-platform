"""Admin for payments. Payments and rail events are read-only (operational record)."""

from django.contrib import admin

from .models import Biller, Payment, RailEvent


@admin.register(Biller)
class BillerAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "network", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(_ReadOnly):
    list_display = ("reference", "purpose", "status", "amount_minor", "fee_minor",
                    "network", "rail", "created_at", "completed_at")
    list_filter = ("purpose", "status", "network", "rail", "mode")
    search_fields = ("reference", "rail_ref", "payer", "account_ref")
    date_hierarchy = "created_at"


@admin.register(RailEvent)
class RailEventAdmin(_ReadOnly):
    list_display = ("rail", "provider_ref", "status", "signature_ok", "created_at")
    list_filter = ("rail", "status", "signature_ok")
    search_fields = ("provider_ref",)
