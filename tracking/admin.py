from django.contrib import admin

from .models import Event, Participant, TelegramConfig


@admin.register(TelegramConfig)
class TelegramConfigAdmin(admin.ModelAdmin):
    list_display = ("group_id", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not TelegramConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "group_id", "schedule_starts_at", "schedule_ends_at", "created_at")
    readonly_fields = (
        "group_id",
        "route_segments",
        "enrollment_token",
        "spectator_token",
        "created_at",
        "opened_at",
        "closed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("display_name", "event", "user", "laps", "consented_at", "withdrawn", "position_at")
    readonly_fields = [field.name for field in Participant._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
