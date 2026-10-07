from django.db import models


class Budget(models.Model):
    """A spending budget set on a billing account in the provider's console.

    Known from the provider's budget API, or only from its notifications when
    the budgets cannot be listed (then amount, period and scope may be blank).
    """

    class Period(models.TextChoices):
        MONTH = "month", "Monthly"
        QUARTER = "quarter", "Quarterly"
        YEAR = "year", "Yearly"
        CUSTOM = "custom", "Custom"

    billing_account = models.ForeignKey("billing.BillingAccount", on_delete=models.CASCADE, related_name="budgets")
    # GCP: the budget's ID, the last part of billingAccounts/<id>/budgets/<id>
    external_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True, default="")
    # Null when the amount follows last period's spend
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    period = models.CharField(max_length=20, choices=Period.choices, blank=True, default="")
    # Set for a custom period; end_date is null when it never ends
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    # GCP: INCLUDE_ALL_CREDITS, EXCLUDE_ALL_CREDITS or INCLUDE_SPECIFIED_CREDITS
    credit_treatment = models.CharField(max_length=50, blank=True, default="")
    # GCP: projects (by number) the budget is limited to; empty means every project of the billing account
    projects = models.JSONField(default=list, blank=True)
    # Ancestors, services, labels, ... the budget is also limited by; it may cover less than the projects
    has_other_filters = models.BooleanField(default=False)
    pubsub_topic = models.CharField(max_length=500, blank=True, default="")
    # False once the budget API stops returning it
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["billing_account", "external_id"], name="unique_billing_budget"),
        ]

    def __str__(self):
        # Budget names are not unique either
        return f"{self.name} ({self.external_id})" if self.name else self.external_id
