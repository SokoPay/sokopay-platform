from django.contrib import admin

from .models import FinancialProduct, FinancialProvider, ProductApplication


class ProductInline(admin.TabularInline):
    model = FinancialProduct
    extra = 0


@admin.register(FinancialProvider)
class FinancialProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "regulator", "connector", "is_active")
    list_filter = ("category", "is_active")
    inlines = [ProductInline]


@admin.register(ProductApplication)
class ProductApplicationAdmin(admin.ModelAdmin):
    list_display = ("reference", "product", "status", "consent_given_at", "created_at")
    list_filter = ("status",)
    search_fields = ("reference", "partner_ref", "user__phone")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
