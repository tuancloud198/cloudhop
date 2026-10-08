from django.contrib import admin

from accounts.models.account import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "provider",
        "external_id",
        "project_id",
        "credential_ref",
        "added_at",
        "updated_at",
        "is_valid",
        "is_active",
    )
    list_filter = ("id", "name", "provider", "external_id", "credential_ref")
