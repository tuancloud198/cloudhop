from django.contrib import admin

from kubernetes.models import KubeResource


@admin.register(KubeResource)
class KubeResourceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "cluster",
        "kind",
        "namespace",
        "name",
        "group",
        "version",
        "created_at",
    )
    list_filter = ("cluster", "kind", "group")
    search_fields = ("name", "namespace", "uid")
    readonly_fields = ("created_at",)
