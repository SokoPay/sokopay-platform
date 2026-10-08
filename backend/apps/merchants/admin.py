from django.contrib import admin

from .models import (
    ApiKey,
    Merchant,
    MerchantDocument,
    MerchantMember,
    Settlement,
    SettlementAccount,
)


class MemberInline(admin.TabularInline):
    model = MerchantMember
    extra = 0


class SettlementAccountInline(admin.TabularInline):
    model = SettlementAccount
    extra = 0


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("trading_name", "legal_name", "business_type", "status", "risk_tier",
                    "created_at")
    list_filter = ("status", "business_type", "risk_tier")
    search_fields = ("legal_name", "trading_name", "tin", "registration_no")
    inlines = [MemberInline, SettlementAccountInline]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("prefix", "merchant", "mode", "last_used_at", "revoked_at")
    list_filter = ("mode",)
    search_fields = ("prefix",)
    readonly_fields = ("key_hash",)  # never editable; secret is never stored anyway


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Settlement)
class SettlementAdmin(_ReadOnly):
    list_display = ("id", "merchant", "amount_minor", "status", "requested_by",
                    "approved_by", "created_at")
    list_filter = ("status",)


admin.site.register(MerchantDocument)
