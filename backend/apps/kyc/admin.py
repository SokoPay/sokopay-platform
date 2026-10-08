"""KYC admin. Compliance staff can place and release wallet holds (each action logged)."""

from django.contrib import admin, messages

from . import services
from .models import KycProfile


@admin.register(KycProfile)
class KycProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "ghana_card_masked", "verified_at", "frozen")
    list_filter = ("tier", "frozen")
    search_fields = ("user__phone", "verified_name")
    # The card number is never shown in full or editable here.
    exclude = ("ghana_card_number", "ghana_card_hash")
    readonly_fields = ("user", "tier", "verified_name", "verified_at",
                       "frozen", "frozen_reason", "frozen_at", "frozen_by")
    actions = ["place_hold", "release_hold"]

    @admin.action(description="Place compliance hold (block money leaving the wallet)")
    def place_hold(self, request, queryset):
        for profile in queryset:
            services.freeze(profile.user, reason="Compliance hold placed in admin",
                            actor=request.user)
        messages.success(request, f"Hold placed on {queryset.count()} wallet(s).")

    @admin.action(description="Release compliance hold")
    def release_hold(self, request, queryset):
        for profile in queryset:
            services.unfreeze(profile.user, actor=request.user)
        messages.success(request, f"Hold released on {queryset.count()} wallet(s).")
