from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models.account import Account
from clusters.models.cluster import Clusters
from common.kube.client import KubeAPIError
from moves.models.move import Move
from moves.services.move import advance, cancel_move, create_move, retry_move
from moves.tasks import advance_move, resume_moves

VELERO = "/apis/velero.io/v1/namespaces/velero"
NOT_FOUND = KubeAPIError("not found", 404)


class FakeKube:
    """A Kubernetes API answering from canned responses.

    A list of responses is answered one per call, the last one for every later call;
    a KubeAPIError is raised. GET of an unknown path is a 404, a list of one is empty.
    """

    def __init__(self):
        self.responses = {}
        self.lists = {}
        self.posts = []
        self.patches = []

    def get(self, path, params=None):
        return self._answer(self.responses, path, NOT_FOUND)

    def list(self, path):
        return self._answer(self.lists, path, [])

    def post(self, path, body):
        self.posts.append((path, body))
        return body

    def patch(self, path, body):
        self.patches.append((path, body))
        return body

    def _answer(self, answers, path, missing):
        answer = answers.get(path, missing)
        if isinstance(answer, list) and answers is self.responses:
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def velero_ready(kube, read_only=False, bucket="cloudhop-moves"):
    kube.responses["/apis/velero.io/v1"] = {}
    kube.responses[f"{VELERO}/backupstoragelocations/default"] = {
        "spec": {"objectStorage": {"bucket": bucket}, **({"accessMode": "ReadOnly"} if read_only else {})},
        "status": {"phase": "Available"},
    }


class MoveTestCase(APITestCase):
    def setUp(self):
        self.source_cluster = self.cluster("source")
        self.target_cluster = self.cluster("target")
        self.kubes = {"source": FakeKube(), "target": FakeKube()}

        @contextmanager
        def fake_kube(cluster):
            yield self.kubes[cluster.name]

        patch = mock.patch("moves.services.move._kube", fake_kube)
        patch.start()
        self.addCleanup(patch.stop)

    def cluster(self, name):
        account = Account.objects.create(
            name=name, provider="gcp", external_id=name, project_id=f"{name}-project",
            credential_ref="/unused.json", is_valid=True,
        )
        return Clusters.objects.create(
            account_id=account, name=name, location="asia-southeast1", external_id=name, status="RUNNING",
        )

    def move(self, mode=Move.Mode.CUTOVER, **fields):
        return create_move(self.source_cluster, self.target_cluster, ["shop"], mode=mode, **fields)

    def ready_to_check(self):
        """Both clusters ready: Velero on one bucket, a shop namespace with one volume in the source only."""
        source, target = self.kubes["source"], self.kubes["target"]
        velero_ready(source)
        velero_ready(target, read_only=True)
        source.responses["/api/v1/namespaces/shop"] = {}
        source.lists["/api/v1/namespaces/shop/persistentvolumeclaims"] = [{"spec": {"storageClassName": "standard-rwo"}}]
        source.lists["/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses"] = [
            {"metadata": {"labels": {"velero.io/csi-volumesnapshot-class": "true"}}}
        ]
        target.responses["/apis/storage.k8s.io/v1/storageclasses/standard-rwo"] = {}

    def advance_until_waiting(self, move):
        """Run steps until one waits or the move ends; returns what the last run returned."""
        for _ in range(10):
            delay = advance(move.pk)
            if delay != 0:
                move.refresh_from_db()
                return delay
        self.fail("the move never waited or ended")


class MoveRunTests(MoveTestCase):
    def test_cutover_runs_every_step(self):
        self.ready_to_check()
        source, target = self.kubes["source"], self.kubes["target"]
        source.lists["/apis/apps/v1/namespaces/shop/deployments"] = [{"metadata": {"name": "web"}, "spec": {"replicas": 2}}]
        scale = "/apis/apps/v1/namespaces/shop/deployments/web/scale"
        source.responses[scale] = [{"spec": {"replicas": 2}, "status": {"replicas": 2}}, {"spec": {"replicas": 0}, "status": {}}]
        move = self.move()
        backup = f"{VELERO}/backups/cloudhop-move-{move.pk}-1"

        # Checked, then waiting for the source pods to stop
        self.assertEqual(self.advance_until_waiting(move), 15)
        self.assertEqual((move.status, move.replicas), (Move.Status.SCALING_DOWN, {"shop/Deployment/web": 2}))
        self.assertEqual(source.patches, [(scale, {"spec": {"replicas": 0}})])

        source.responses[backup] = [{"status": {"phase": "InProgress", "progress": {"totalItems": 10, "itemsBackedUp": 3}}}]
        self.advance_until_waiting(move)
        self.assertEqual((move.status, move.waiting_on), (Move.Status.BACKING_UP, "Backup InProgress: 3 of 10 objects"))
        path, body = source.posts[0]
        self.assertEqual(path, f"{VELERO}/backups")
        self.assertEqual(body["spec"]["includedNamespaces"], ["shop"])
        self.assertTrue(body["spec"]["snapshotMoveData"])

        source.responses[backup] = [{"status": {"phase": "Completed", "progress": {"itemsBackedUp": 10}}}]
        # The target's Velero has not listed the bucket yet
        target.responses[backup] = [NOT_FOUND]
        self.advance_until_waiting(move)
        self.assertEqual(move.status, Move.Status.RESTORING)
        self.assertEqual(target.posts, [])

        target.responses[backup] = [{"status": {"phase": "Completed"}}]
        target.responses[f"{VELERO}/restores/cloudhop-move-{move.pk}-1"] = [{"status": {"phase": "Completed"}}]
        target.responses[scale] = [{"spec": {"replicas": 0}}]
        target.lists["/apis/apps/v1/namespaces/shop/deployments"] = [
            {"metadata": {"name": "web"}, "spec": {"replicas": 2}, "status": {"readyReplicas": 1}}
        ]
        self.advance_until_waiting(move)
        self.assertEqual(move.status, Move.Status.VERIFYING)
        self.assertEqual(target.posts[0][1]["spec"]["backupName"], f"cloudhop-move-{move.pk}-1")
        self.assertEqual(target.patches, [(scale, {"spec": {"replicas": 2}})])
        self.assertIn("Deployment shop/web 1/2 ready", move.waiting_on)

        target.lists["/apis/apps/v1/namespaces/shop/deployments"][0]["status"]["readyReplicas"] = 2
        self.assertIsNone(self.advance_until_waiting(move))
        self.assertEqual(move.status, Move.Status.DONE)
        self.assertIsNotNone(move.finished_at)

    def test_copy_mode_leaves_the_source_running(self):
        self.ready_to_check()
        source = self.kubes["source"]
        source.lists["/apis/apps/v1/namespaces/shop/deployments"] = [{"metadata": {"name": "web"}, "spec": {"replicas": 2}}]
        move = self.move(mode=Move.Mode.COPY)
        source.responses[f"{VELERO}/backups/cloudhop-move-{move.pk}-1"] = [{"status": {"phase": "InProgress"}}]

        self.advance_until_waiting(move)

        self.assertEqual(move.status, Move.Status.BACKING_UP)
        self.assertEqual((move.replicas, source.patches), ({}, []))

    def test_failed_backup_is_retried_under_a_new_name(self):
        self.ready_to_check()
        move = self.move(mode=Move.Mode.COPY)
        self.kubes["source"].responses[f"{VELERO}/backups/cloudhop-move-{move.pk}-1"] = [
            {"status": {"phase": "Failed", "failureReason": "bucket not reachable"}}
        ]

        self.assertIsNone(self.advance_until_waiting(move))
        self.assertEqual((move.status, move.failed_step), (Move.Status.FAILED, Move.Status.BACKING_UP))
        self.assertIn("bucket not reachable", move.error)

        retry_move(move.pk)
        self.kubes["source"].responses[f"{VELERO}/backups/cloudhop-move-{move.pk}-2"] = [{"status": {"phase": "New"}}]
        self.advance_until_waiting(move)

        self.assertEqual((move.status, move.backup_name), (Move.Status.BACKING_UP, f"cloudhop-move-{move.pk}-2"))

    def test_retried_scale_down_keeps_the_replicas_recorded_before(self):
        move = self.move()
        move.status, move.replicas = Move.Status.SCALING_DOWN, {"shop/Deployment/web": 3}
        move.save()
        # Already scaled down by the first run
        self.kubes["source"].lists["/apis/apps/v1/namespaces/shop/deployments"] = [
            {"metadata": {"name": "web"}, "spec": {"replicas": 0}}
        ]
        self.kubes["source"].responses["/apis/apps/v1/namespaces/shop/deployments/web/scale"] = [
            {"spec": {"replicas": 0}, "status": {}}
        ]
        self.kubes["source"].responses[f"{VELERO}/backups/cloudhop-move-{move.pk}-1"] = [{"status": {}}]

        self.advance_until_waiting(move)

        self.assertEqual((move.status, move.replicas), (Move.Status.BACKING_UP, {"shop/Deployment/web": 3}))

    def test_storage_classes_are_renamed_in_the_restore(self):
        self.ready_to_check()
        target = self.kubes["target"]
        target.responses["/apis/storage.k8s.io/v1/storageclasses/premium-rwo"] = {}
        move = self.move(mode=Move.Mode.COPY, storage_class_mapping={"standard-rwo": "premium-rwo"})
        move.status, move.backup_name = Move.Status.RESTORING, "cloudhop-move-1-1"
        move.save()
        target.responses[f"{VELERO}/backups/cloudhop-move-1-1"] = [{"status": {"phase": "Completed"}}]
        target.responses[f"{VELERO}/restores/cloudhop-move-{move.pk}-1"] = [{"status": {"phase": "InProgress"}}]

        self.advance_until_waiting(move)

        path, config = target.posts[0]
        self.assertEqual(path, "/api/v1/namespaces/velero/configmaps")
        self.assertEqual(config["data"], {"standard-rwo": "premium-rwo"})
        self.assertEqual(config["metadata"]["labels"]["velero.io/change-storage-class"], "RestoreItemAction")

    def test_waiting_too_long_fails_the_step(self):
        move = self.move()
        move.status, move.replicas = Move.Status.SCALING_DOWN, {"shop/Deployment/web": 1}
        move.step_started_at = timezone.now() - timedelta(minutes=11)
        move.save()
        self.kubes["source"].responses["/apis/apps/v1/namespaces/shop/deployments/web/scale"] = [
            {"spec": {"replicas": 0}, "status": {"replicas": 1}}
        ]

        self.assertIsNone(advance(move.pk))

        move.refresh_from_db()
        self.assertEqual(move.status, Move.Status.FAILED)
        self.assertIn("did not finish within 10 minutes: Pods still running: shop/Deployment/web", move.error)

    def test_cancelled_move_stops(self):
        move = self.move()

        cancel_move(move.pk)

        self.assertIsNone(advance(move.pk))
        move.refresh_from_db()
        self.assertEqual(move.status, Move.Status.CANCELLED)


class MoveCheckTests(MoveTestCase):
    def check_fails(self, message, **fields):
        move = self.move(**fields)
        self.assertIsNone(self.advance_until_waiting(move))
        self.assertEqual((move.status, move.failed_step), (Move.Status.FAILED, Move.Status.CHECKING))
        self.assertIn(message, move.error)

    def test_velero_missing(self):
        self.ready_to_check()
        del self.kubes["target"].responses["/apis/velero.io/v1"]
        self.check_fails("Velero is not installed in the target cluster")

    def test_storage_location_unavailable(self):
        self.ready_to_check()
        self.kubes["source"].responses[f"{VELERO}/backupstoragelocations/default"]["status"]["phase"] = "Unavailable"
        self.check_fails("storage location default in the source cluster is Unavailable")

    def test_different_buckets(self):
        self.ready_to_check()
        velero_ready(self.kubes["target"], bucket="other")
        self.check_fails("uses bucket cloudhop-moves in the source and other in the target")

    def test_namespace_already_in_target(self):
        self.ready_to_check()
        self.kubes["target"].responses["/api/v1/namespaces/shop"] = {}
        self.check_fails("Namespace shop already exists in the target cluster")

    def test_storage_class_missing_in_target(self):
        self.ready_to_check()
        del self.kubes["target"].responses["/apis/storage.k8s.io/v1/storageclasses/standard-rwo"]
        self.check_fails("The target cluster has no storage class standard-rwo")

    def test_mapped_storage_class_missing_in_target(self):
        self.ready_to_check()
        self.check_fails("no storage class standard-rwo (as fast)", storage_class_mapping={"standard-rwo": "fast"})

    def test_no_snapshot_class_for_velero(self):
        self.ready_to_check()
        self.kubes["source"].lists["/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses"] = []
        self.check_fails("no VolumeSnapshotClass labelled velero.io/csi-volumesnapshot-class=true")


class MoveApiTests(MoveTestCase):
    def test_start_a_move(self):
        with mock.patch.object(advance_move, "delay") as delay, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/v1/moves/", {
                "source_cluster": self.source_cluster.pk,
                "target_cluster": self.target_cluster.pk,
                "namespaces": ["shop"],
            }, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.data["mode"], response.data["status"]), ("cutover", "pending"))
        self.assertEqual(response.data["steps"][1], "scaling_down")
        self.assertIn("Created: shop from source to target", response.data["events"][0]["message"])
        delay.assert_called_once_with(response.data["id"])

    def test_rejects_moving_within_one_cluster(self):
        response = self.client.post("/api/v1/moves/", {
            "source_cluster": self.source_cluster.pk, "target_cluster": self.source_cluster.pk, "namespaces": ["shop"],
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("different clusters", response.data["detail"])

    def test_rejects_system_namespaces(self):
        for namespace in ("kube-system", "gke-managed-system", "velero", "default"):
            response = self.client.post("/api/v1/moves/", {
                "source_cluster": self.source_cluster.pk, "target_cluster": self.target_cluster.pk,
                "namespaces": [namespace],
            }, format="json")
            self.assertEqual(response.status_code, 400, namespace)

    def test_one_move_at_a_time_per_cluster(self):
        running = self.move()
        other = self.cluster("other")

        response = self.client.post("/api/v1/moves/", {
            "source_cluster": other.pk, "target_cluster": self.target_cluster.pk, "namespaces": ["shop"],
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn(f"move {running.pk} is still running", response.data["detail"])

    def test_retry_only_a_failed_move(self):
        move = self.move()

        response = self.client.post(f"/api/v1/moves/{move.pk}/retry/")

        self.assertEqual(response.status_code, 400)


class MoveTaskTests(MoveTestCase):
    def test_waiting_move_is_queued_again(self):
        with mock.patch("moves.tasks.advance", return_value=15), mock.patch.object(advance_move, "apply_async") as again:
            advance_move(7)

        again.assert_called_once_with((7,), countdown=15)

    def test_finished_move_is_not_queued_again(self):
        with mock.patch("moves.tasks.advance", return_value=None), mock.patch.object(advance_move, "apply_async") as again:
            advance_move(7)

        again.assert_not_called()

    def test_resume_queues_moves_not_checked_lately(self):
        lost = self.move()
        Move.objects.filter(pk=lost.pk).update(
            status=Move.Status.BACKING_UP, checked_at=timezone.now() - timedelta(minutes=6),
            created_at=timezone.now() - timedelta(hours=1),
        )
        busy = create_move(self.cluster("a"), self.cluster("b"), ["shop"])
        Move.objects.filter(pk=busy.pk).update(status=Move.Status.BACKING_UP, checked_at=timezone.now())

        with mock.patch.object(advance_move, "delay") as delay:
            resume_moves()

        delay.assert_called_once_with(lost.pk)
