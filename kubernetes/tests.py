from unittest import mock

from django.test import TestCase

from accounts.models import Account
from clusters.models import Clusters
from common.cloud import CloudAPIError
from kubernetes.tasks import sync_all_resources, sync_cluster_resources


def cluster(name, account_fields=None, **fields):
    account = Account.objects.create(
        name=name, provider="gcp", external_id=name, project_id=f"{name}-project",
        credential_ref="/unused.json", **{"is_valid": True, **(account_fields or {})},
    )
    return Clusters.objects.create(
        account_id=account, name=name, location="asia-southeast1", external_id=name, status="RUNNING", **fields,
    )


class ResourceTaskTests(TestCase):
    def test_schedule_queues_active_clusters_of_usable_accounts(self):
        usable = cluster("usable")
        cluster("gone", is_active=False)
        cluster("unvalidated", account_fields={"is_valid": False})

        with mock.patch.object(sync_cluster_resources, "apply_async") as apply_async:
            sync_all_resources()

        apply_async.assert_called_once_with((usable.pk,), expires=15 * 60)

    def test_failed_sync_is_logged(self):
        usable = cluster("usable")
        failure = CloudAPIError("cannot reach GCP, try again later")

        with mock.patch("kubernetes.tasks.sync_resources", side_effect=failure), self.assertLogs("kubernetes.tasks", "WARNING") as logs:
            sync_cluster_resources(usable.pk)

        self.assertIn("cannot reach GCP", logs.output[0])

    def test_cluster_disabled_since_queued(self):
        gone = cluster("gone", is_active=False)

        sync_cluster_resources(gone.pk)
