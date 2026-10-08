import logging

from celery import shared_task
from django.conf import settings

from accounts.models.account import Account
from clusters.services.cluster import AccountNotUsable, sync_clusters
from common.cloud.errors import CloudAPIError

logger = logging.getLogger(__name__)


@shared_task
def sync_all_clusters():
    """Queue a cluster sync for every usable account; run on a schedule."""
    for account_id in Account.objects.filter(is_active=True, is_valid=True).values_list("pk", flat=True):
        sync_account_clusters.apply_async((account_id,), expires=settings.SYNC_INTERVAL_MINUTES * 60)


@shared_task
def sync_account_clusters(account_id: int):
    try:
        clusters = sync_clusters(account_id)
    except (Account.DoesNotExist, AccountNotUsable):
        # Deleted or disabled since it was queued
        return
    except CloudAPIError as exc:
        # The next scheduled run tries again
        logger.warning("Cluster sync failed for account %s: %s", account_id, exc)
        return
    logger.info("Synced %d clusters of account %s", len(clusters), account_id)
