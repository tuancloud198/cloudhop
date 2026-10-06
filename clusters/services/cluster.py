from django.db import transaction
from django.utils import timezone

from accounts.models import Account

from ..adapters import ClusterAdapter
from ..models import Clusters

__all__ = ["AccountNotUsable", "sync_clusters"]


class AccountNotUsable(Exception):
    """The account is inactive or its credential was never validated."""


def sync_clusters(account_id: int) -> list[Clusters]:
    """Fetch the account's clusters from its cloud provider and store them.

    New clusters are created, known ones are updated, and clusters no longer
    returned by the provider are marked inactive.

    Raises Account.DoesNotExist, AccountNotUsable, or common.cloud.CloudAPIError.
    """
    account = Account.objects.get(pk=account_id)
    if not account.is_active or not account.is_valid:
        raise AccountNotUsable(f"account {account_id} is inactive or not validated")

    remote_clusters = ClusterAdapter.for_account(account).list_clusters()

    with transaction.atomic():
        synced = []
        for data in remote_clusters:
            cluster, _ = Clusters.objects.update_or_create(
                account_id=account,
                external_id=data["external_id"],
                defaults={**data, "is_active": True},
            )
            synced.append(cluster)

        Clusters.objects.filter(account_id=account, is_active=True).exclude(
            pk__in=[cluster.pk for cluster in synced]
        ).update(is_active=False, updated_at=timezone.now())

    return synced
