from django.contrib import admin

from moves.models import Move, MoveEvent


class MoveEventInline(admin.TabularInline):
    model = MoveEvent
    extra = 0
    readonly_fields = ("created_at", "step", "level", "message")


@admin.register(Move)
class MoveAdmin(admin.ModelAdmin):
    list_display = ("id", "source_cluster", "target_cluster", "mode", "status", "created_at")
    list_filter = ("status", "mode")
    inlines = [MoveEventInline]
