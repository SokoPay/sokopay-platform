from django.contrib import admin

from .models import ReconItem, ReconRun


class ReconItemInline(admin.TabularInline):
    model = ReconItem
    extra = 0
    readonly_fields = ("kind", "rail_ref", "ours_minor", "theirs_minor")


@admin.register(ReconRun)
class ReconRunAdmin(admin.ModelAdmin):
    list_display = ("rail", "date", "matched_count", "break_count", "created_at")
    list_filter = ("rail", "date")
    inlines = [ReconItemInline]

    def has_add_permission(self, request):
        return False


@admin.register(ReconItem)
class ReconItemAdmin(admin.ModelAdmin):
    list_display = ("run", "kind", "rail_ref", "ours_minor", "theirs_minor", "resolved")
    list_filter = ("kind", "resolved")
    search_fields = ("rail_ref",)
