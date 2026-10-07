from django.db import models


class AccountBilling(models.Model):
    """Which billing account pays for a cloud account, as of the last billing sync."""

    account = models.OneToOneField("accounts.Account", on_delete=models.CASCADE, related_name="billing")
    # Null when the account has no billing account linked
    billing_account = models.ForeignKey(
        "billing.BillingAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_links",
    )
    billing_enabled = models.BooleanField(default=False)
    # GCP: project number; budgets name the projects they cover by number, not ID
    project_number = models.CharField(max_length=50, blank=True, default="")
    synced_at = models.DateTimeField()
