import logging

from celery import shared_task
from django.conf import settings

from accounts.models.account import Account
from billing.services.billing import AccountNotUsable, sync_billing
from common.cloud.errors import CloudAPIError

logger = logging.getLogger(__name__)


@shared_task
def sync_all_billing():
    """Queue a billing sync for every usable account; run on a schedule."""
    for account_id in Account.objects.filter(is_active=True, is_valid=True).values_list("pk", flat=True):
        sync_account_billing.apply_async((account_id,), expires=settings.SYNC_INTERVAL_MINUTES * 60)


@shared_task
def sync_account_billing(account_id: int):
    try:
        result = sync_billing(account_id)
    except (Account.DoesNotExist, AccountNotUsable):
        # Deleted or disabled since it was queued
        return
    except CloudAPIError as exc:
        # The next scheduled run tries again
        logger.warning("Billing sync failed for account %s: %s", account_id, exc)
        return
    for warning in result["warnings"]:
        logger.warning("Billing sync of account %s: %s", account_id, warning)
    logger.info("Synced billing of account %s, %d spend updates received", account_id, result["received"])
