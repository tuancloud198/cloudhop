from django.db import models

# Create your models here.


# Currently only support three providers: GCP, AWS and Azure
class Account(models.Model):
    class Provider(models.TextChoices):
        GCP = "gcp", "Google Cloud"
        AWS = "aws", "AWS"
        AZURE = "azure", "Azure"

    id = models.BigAutoField(primary_key=True)

    name = models.CharField(max_length=100)
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
    )

    # Provider-specific identifier
    # GCP: project_id
    # AWS: account_id
    external_id = models.CharField(max_length=255)
    # Credential Ref store the absolute path to the service account of each account
    credential_ref = models.CharField(max_length=255, null=False, blank=False)
    is_active = models.BooleanField(default=True)

    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="unique_cloud_account",
            )
        ]
