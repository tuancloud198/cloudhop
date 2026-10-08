import logging

from celery import shared_task
from django.conf import settings

from clusters.models import Clusters
from common.cloud import CloudAPIError
from kubernetes.services import AccountNotUsable, ClusterNotUsable, sync_resources

logger = logging.getLogger(__name__)


@shared_task
def sync_all_resources():
    """Queue a resource sync for every active cluster of a usable account; run on a schedule."""
    clusters = Clusters.objects.filter(is_active=True, account_id__is_active=True, account_id__is_valid=True)
    for cluster_id in clusters.values_list("pk", flat=True):
        sync_cluster_resources.apply_async((cluster_id,), expires=settings.SYNC_INTERVAL_MINUTES * 60)


@shared_task
def sync_cluster_resources(cluster_id: int):
    try:
        result = sync_resources(cluster_id)
    except (Clusters.DoesNotExist, ClusterNotUsable, AccountNotUsable):
        # Deleted or disabled since it was queued
        return
    except CloudAPIError as exc:
        # The next scheduled run tries again
        logger.warning("Resource sync failed for cluster %s: %s", cluster_id, exc)
        return
    logger.info(
        "Synced %d resources of cluster %s, %d kinds skipped",
        sum(result["synced"].values()), cluster_id, len(result["skipped"]),
    )
