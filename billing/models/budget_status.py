from django.db import models


class BudgetStatus(models.Model):
    """Spend against a budget, as reported by one provider notification.

    Kept as history so the burn rate can be worked out; the latest by
    published_at is the current status.
    """

    budget = models.ForeignKey("billing.Budget", on_delete=models.CASCADE, related_name="statuses")
    # Notifications can be delivered more than once
    message_id = models.CharField(max_length=255, unique=True)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2)
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    # Start of the budget period the cost is counted from
    interval_start = models.DateTimeField()
    # Highest threshold crossed, as a fraction (0.9 = 90%); null when none
    threshold_exceeded = models.FloatField(null=True, blank=True)
    forecast_threshold_exceeded = models.FloatField(null=True, blank=True)
    published_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["budget", "-published_at"])]
