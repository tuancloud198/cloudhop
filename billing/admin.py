from django.contrib import admin

from billing.models.account_billing import AccountBilling
from billing.models.billing_account import BillingAccount
from billing.models.budget import Budget
from billing.models.budget_status import BudgetStatus


@admin.register(BillingAccount)
class BillingAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "provider", "external_id", "currency", "is_open", "pubsub_subscription", "updated_at")
    list_filter = ("provider", "is_open")
    search_fields = ("name", "external_id")


@admin.register(AccountBilling)
class AccountBillingAdmin(admin.ModelAdmin):
    list_display = ("account", "billing_account", "billing_enabled", "project_number", "synced_at")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "billing_account", "amount", "currency", "period", "start_date", "end_date", "is_active")
    list_filter = ("billing_account", "period", "is_active")
    search_fields = ("name", "external_id")


@admin.register(BudgetStatus)
class BudgetStatusAdmin(admin.ModelAdmin):
    list_display = ("budget", "cost_amount", "budget_amount", "currency", "threshold_exceeded", "published_at")
    list_filter = ("budget",)
