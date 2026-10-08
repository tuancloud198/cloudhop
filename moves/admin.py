from django.contrib import admin

from moves.models.move import Move
from moves.models.move_event import MoveEvent


class MoveEventInline(admin.TabularInline):
    model = MoveEvent
    extra = 0
    readonly_fields = ("created_at", "step", "level", "message")


@admin.register(Move)
class MoveAdmin(admin.ModelAdmin):
    list_display = ("id", "source_cluster", "target_cluster", "method", "mode", "status", "created_at")
    list_filter = ("status", "method", "mode")
    inlines = [MoveEventInline]
