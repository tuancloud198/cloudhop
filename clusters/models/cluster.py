from django.db import models

# Create your models here.


class Clusters(models.Model):
    account_id = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=100)
    external_id = models.CharField(max_length=500)
    status = models.CharField(max_length=50)
    kubernetes_version = models.CharField(
        max_length=50,
        null=True,
        blank=True,
    )
    # Storing configs regarding the cluster,
    # need to normalize for each Provider to spinup the same resources accross multiple providers
    spec = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account_id", "external_id"],
                name="unique_account_cluster",
            )
        ]
