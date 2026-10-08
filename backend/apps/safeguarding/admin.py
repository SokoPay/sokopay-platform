from django.contrib import admin

from .models import SafeguardingCheck, TrustAccount, TrustBalance


@admin.register(TrustAccount)
class TrustAccountAdmin(admin.ModelAdmin):
    list_display = ("bank_name", "account_name", "account_last4", "connector", "is_active")


@admin.register(TrustBalance)
class TrustBalanceAdmin(admin.ModelAdmin):
    list_display = ("account", "balance_minor", "as_of", "source", "recorded_by")
    list_filter = ("account", "source")

    def has_change_permission(self, request, obj=None):
        return False   # balances are a record; correct by adding a new one

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SafeguardingCheck)
class SafeguardingCheckAdmin(admin.ModelAdmin):
    list_display = ("created_at", "status", "liabilities_minor", "trust_total_minor", "surplus_minor")
    list_filter = ("status",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
