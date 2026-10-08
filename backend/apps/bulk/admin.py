from django.contrib import admin

from .models import BulkPayout, BulkPayoutItem


class ItemInline(admin.TabularInline):
    model = BulkPayoutItem
    extra = 0
    can_delete = False
    fields = ("row_number", "recipient_name", "destination_type", "institution", "account",
              "amount_minor", "fee_minor", "status", "error", "provider_ref")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BulkPayout)
class BulkPayoutAdmin(admin.ModelAdmin):
    """Read-only: batches change only through apps.bulk.services (maker-checker + ledger)."""

    list_display = ("created_at", "merchant", "note", "status", "valid_count", "paid_count",
                    "failed_count", "total_amount_minor", "self_approved")
    list_filter = ("status", "self_approved")
    search_fields = ("note", "source_filename", "merchant__legal_name")
    readonly_fields = [f.name for f in BulkPayout._meta.fields]
    inlines = [ItemInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
