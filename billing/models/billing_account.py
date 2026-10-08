from django.db import models

from accounts.models.account import Account


class BillingAccount(models.Model):
    """What pays for one or more cloud accounts; budgets and credits belong to it.

    GCP: a Cloud Billing account, shared by every project linked to it.
    """

    provider = models.CharField(max_length=20, choices=Account.Provider.choices)
    # GCP: billing account ID, e.g. 012345-ABCDEF-678901
    external_id = models.CharField(max_length=255)
    # Falls back to external_id when the details cannot be read
    name = models.CharField(max_length=255, blank=True, default="")
    currency = models.CharField(max_length=3, blank=True, default="")
    is_open = models.BooleanField(default=True)
    # GCP: Pub/Sub subscription receiving budget notifications, projects/<project>/subscriptions/<name>
    pubsub_subscription = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "external_id"], name="unique_billing_account"),
        ]

    def __str__(self):
        # Names are not unique (GCP calls every new one "My Billing Account"), so show the ID too
        return f"{self.name} ({self.external_id})" if self.name else self.external_id
