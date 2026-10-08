"""
Read-only admin for the ledger.

The ledger is append-only, so the admin never allows adding, changing or deleting
postings, entries or balances by hand. It exists for inspection and investigation
only. Corrections are made by posting reversing entries through code, never here.
"""

from django.contrib import admin

from .models import AccountBalance, Currency, JournalEntry, LedgerAccount, Posting


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PostingInline(admin.TabularInline):
    model = Posting
    extra = 0
    can_delete = False
    readonly_fields = ("account", "amount", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(ReadOnlyAdmin):
    list_display = ("id", "narrative", "reference_type", "reference_id", "created_at")
    search_fields = ("narrative", "reference_id", "idempotency_key")
    inlines = [PostingInline]


@admin.register(LedgerAccount)
class LedgerAccountAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "account_type", "currency", "is_active")
    list_filter = ("account_type", "currency", "is_active")
    search_fields = ("code", "name")


@admin.register(AccountBalance)
class AccountBalanceAdmin(ReadOnlyAdmin):
    list_display = ("account", "balance", "version", "updated_at")
    search_fields = ("account__code",)


admin.site.register(Currency, ReadOnlyAdmin)
