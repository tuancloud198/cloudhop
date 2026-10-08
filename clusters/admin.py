from django.contrib import admin

from clusters.models.cluster import Clusters


@admin.register(Clusters)
class ClustersAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "account_id",
        "location",
        "status",
        "spec",
        "kubernetes_version",
        "is_active",
        "updated_at",
    )
    list_filter = ("account_id", "status", "location", "is_active")
    search_fields = ("name", "external_id")
    readonly_fields = ("created_at", "updated_at")
