from unittest import mock

from django.test import TestCase

from accounts.models.account import Account
from clusters.tasks import sync_account_clusters, sync_all_clusters
from common.cloud.errors import CloudAPIError


def account(name, **fields):
    return Account.objects.create(
        name=name, provider="gcp", external_id=name, project_id=f"{name}-project",
        credential_ref="/unused.json", **{"is_valid": True, **fields},
    )


class ClusterTaskTests(TestCase):
    def test_schedule_queues_usable_accounts_only(self):
        usable = account("usable")
        account("inactive", is_active=False)
        account("unvalidated", is_valid=False)

        with mock.patch.object(sync_account_clusters, "apply_async") as apply_async:
            sync_all_clusters()

        apply_async.assert_called_once_with((usable.pk,), expires=15 * 60)

    def test_failed_sync_is_logged(self):
        usable = account("usable")
        failure = CloudAPIError("permission denied on GCP project usable-project")

        with mock.patch("clusters.tasks.sync_clusters", side_effect=failure), self.assertLogs("clusters.tasks", "WARNING") as logs:
            sync_account_clusters(usable.pk)

        self.assertIn("permission denied", logs.output[0])

    def test_account_deleted_since_queued(self):
        # Nothing to sync and nothing to report
        sync_account_clusters(12345)
