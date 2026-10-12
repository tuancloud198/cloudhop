from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models.account import Account
from clusters.models.cluster import Clusters
from common.cloud.errors import CloudAPIError
from common.kube.client import KubeAPIError
from moves.adapters.gcp import GCPBucketAdapter
from moves.models.move import Move
from moves.services.move import advance, cancel_move, create_move, read_velero, retry_move, velero_problems
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
        self.applies = []

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

    def apply(self, path, body, field_manager):
        answer = self.responses.get(f"apply {path}")
        if isinstance(answer, Exception):
            raise answer
        self.applies.append((path, body))
        return body

    def _answer(self, answers, path, missing):
        answer = answers.get(path, missing)
        if isinstance(answer, list) and answers is self.responses:
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def velero_ready(kube, read_only=False, bucket="cloudhop-moves", version="v1.18.4", plugin="v1.14.4",
                 features="EnableCSI"):
    kube.responses["/apis/velero.io/v1"] = {}
    # As velero install creates it
    kube.responses["/apis/apps/v1/namespaces/velero/deployments/velero"] = {"spec": {"template": {"spec": {
        "containers": [{"name": "velero", "image": f"velero/velero:{version}",
                        "args": ["server", f"--features={features}", "--uploader-type=kopia"]}],
        "initContainers": [{"name": "velero-velero-plugin-for-gcp", "image": f"velero/velero-plugin-for-gcp:{plugin}"}],
    }}}}
    kube.responses[f"{VELERO}/backupstoragelocations/default"] = {
        "spec": {"objectStorage": {"bucket": bucket}, **({"accessMode": "ReadOnly"} if read_only else {})},
        "status": {"phase": "Available"},
    }


def serves(kube, kinds, custom=()):
    """Discovery of the given built-in kinds, and custom ones, as (group, resource, kind, namespaced)."""
    groups = {}
    for group, resource, kind, namespaced, label in [(*k, "onstart") for k in kinds] + [(*k, "true") for k in custom]:
        groups.setdefault((group, label), []).append(
            {"name": resource, "kind": kind, "namespaced": namespaced, "verbs": ["get", "list"]}
        )
    kube.responses["/apis/apiregistration.k8s.io/v1/apiservices"] = {"items": [
        {"spec": {"group": group, "version": "v1"},
         "metadata": {"labels": {"kube-aggregator.kubernetes.io/automanaged": label}}}
        for group, label in groups
    ]}
    kube.responses["/apis"] = {"groups": [
        {"name": group, "preferredVersion": {"version": "v1"}} for group, _ in groups if group
    ]}
    for (group, _), resources in groups.items():
        kube.responses[f"/apis/{group}/v1" if group else "/api/v1"] = {"resources": resources}


CORE_KINDS = [
    ("", "namespaces", "Namespace", False),
    ("", "secrets", "Secret", True),
    ("", "configmaps", "ConfigMap", True),
    ("", "services", "Service", True),
    ("", "serviceaccounts", "ServiceAccount", True),
    ("", "persistentvolumeclaims", "PersistentVolumeClaim", True),
    ("apps", "deployments", "Deployment", True),
    ("apps", "replicasets", "ReplicaSet", True),
    ("batch", "jobs", "Job", True),
]


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
            {"metadata": {"name": "velero-pd", "labels": {"velero.io/csi-volumesnapshot-class": "true"}}}
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

        source.responses[backup] = [{"status": {"phase": "Completed", "progress": {"itemsBackedUp": 10},
                                                "backupItemOperationsCompleted": 1}}]
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

    def test_velero_without_csi_support(self):
        self.ready_to_check()
        velero_ready(self.kubes["source"], features="")
        self.check_fails("Velero in the source cluster cannot copy these namespaces: its CSI support is off")

    def test_velero_without_csi_support_is_fine_without_volumes(self):
        self.ready_to_check()
        velero_ready(self.kubes["source"], features="")
        self.kubes["source"].lists["/api/v1/namespaces/shop/persistentvolumeclaims"] = []
        move = self.move()
        self.advance_until_waiting(move)
        self.assertNotEqual(move.failed_step, Move.Status.CHECKING, move.error)
        self.assertTrue(move.events.filter(message__startswith="Checked:").exists())

    def test_gcp_plugin_not_matching_velero(self):
        self.ready_to_check()
        velero_ready(self.kubes["target"], read_only=True, plugin="v1.12.0")
        self.check_fails("its GCP plugin v1.12.0 does not work with Velero v1.18.4; it needs v1.14.x")

    def test_different_velero_versions_are_a_warning(self):
        self.ready_to_check()
        velero_ready(self.kubes["target"], read_only=True, version="v1.17.1", plugin="v1.13.2")
        move = self.move()
        self.advance_until_waiting(move)
        self.assertNotEqual(move.failed_step, Move.Status.CHECKING, move.error)
        self.assertTrue(move.events.filter(level="warning", message__contains="v1.18.4 in the source").exists())

    def test_backup_without_volume_data_fails(self):
        self.ready_to_check()
        move = self.move(mode=Move.Mode.COPY)
        self.kubes["source"].responses[f"{VELERO}/backups/cloudhop-move-{move.pk}-1"] = [
            {"status": {"phase": "Completed", "warnings": 1}}
        ]
        self.assertIsNone(self.advance_until_waiting(move))
        self.assertEqual((move.status, move.failed_step), (Move.Status.FAILED, Move.Status.BACKING_UP))
        self.assertIn("copied the data of 0 of 1 volume", move.error)
        self.assertIn("--features=EnableCSI", move.error)

    def test_no_snapshot_class_for_velero(self):
        self.ready_to_check()
        self.kubes["source"].lists["/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses"] = []
        self.check_fails("no VolumeSnapshotClass labelled velero.io/csi-volumesnapshot-class=true")


class ManifestMoveTests(MoveTestCase):
    def ready_to_copy(self):
        """A shop namespace in the source only, with a few objects and no volumes."""
        source = self.kubes["source"]
        serves(source, CORE_KINDS)
        source.responses["/api/v1/namespaces/shop"] = {
            "metadata": {"name": "shop", "uid": "u1", "labels": {"team": "a"}},
            "spec": {"finalizers": ["kubernetes"]}, "status": {"phase": "Active"},
        }
        meta = lambda name, **extra: {"name": name, "namespace": "shop", "uid": "x", "resourceVersion": "9", **extra}
        source.lists["/api/v1/namespaces/shop/secrets"] = [{"metadata": meta("db"), "data": {"password": "cw=="}}]
        source.lists["/api/v1/namespaces/shop/configmaps"] = [
            {"metadata": meta("kube-root-ca.crt"), "data": {}},
            {"metadata": meta("settings"), "data": {"mode": "live"}},
        ]
        source.lists["/api/v1/namespaces/shop/services"] = [{
            "metadata": meta("web"),
            "spec": {"type": "NodePort", "clusterIP": "10.0.0.7", "clusterIPs": ["10.0.0.7"],
                     "ports": [{"port": 80, "nodePort": 31000}]},
        }]
        source.lists["/api/v1/namespaces/shop/serviceaccounts"] = [{"metadata": meta("default")}]
        source.lists["/apis/apps/v1/namespaces/shop/deployments"] = [{
            "metadata": meta("web", annotations={"deployment.kubernetes.io/revision": "3"}, finalizers=["x"]),
            "spec": {"replicas": 2}, "status": {"readyReplicas": 2},
        }]
        source.lists["/apis/apps/v1/namespaces/shop/replicasets"] = [
            {"metadata": meta("web-abc", ownerReferences=[{"kind": "Deployment", "name": "web"}])}
        ]
        source.lists["/apis/batch/v1/namespaces/shop/jobs"] = [{
            "metadata": meta("migrate"), "spec": {"selector": {}},
            "status": {"conditions": [{"type": "Complete", "status": "True"}]},
        }]

    def manifest_move(self, mode=Move.Mode.COPY):
        return self.move(mode=mode, method=Move.Method.MANIFESTS)

    def test_copy_applies_the_objects_in_order_and_leaves_the_source_running(self):
        self.ready_to_copy()
        source, target = self.kubes["source"], self.kubes["target"]
        move = self.manifest_move()

        self.assertIsNone(self.advance_until_waiting(move))

        self.assertEqual(move.status, Move.Status.DONE)
        self.assertEqual([path for path, _ in target.applies], [
            "/api/v1/namespaces/shop",
            "/api/v1/namespaces/shop/secrets/db",
            "/api/v1/namespaces/shop/configmaps/settings",
            "/api/v1/namespaces/shop/services/web",
            "/apis/apps/v1/namespaces/shop/deployments/web",
        ])
        namespace, _, _, service, deployment = (body for _, body in target.applies)
        self.assertEqual(namespace, {"apiVersion": "v1", "kind": "Namespace",
                                     "metadata": {"name": "shop", "labels": {"team": "a"}}})
        self.assertEqual(service["spec"], {"type": "NodePort", "ports": [{"port": 80}]})
        self.assertEqual(deployment["metadata"], {"name": "web", "namespace": "shop"})
        self.assertEqual(deployment["spec"], {"replicas": 2})
        self.assertEqual((source.posts, source.patches, source.applies), ([], [], []))
        messages = [event.message for event in move.events.all()]
        self.assertIn("Checked: 6 objects to copy, no volumes", messages)
        self.assertIn("Not copied, since they already finished: Job shop/migrate", messages)
        self.assertIn("Applied 5 objects in the target: 1 ConfigMap, 1 Deployment, 1 Namespace, 1 Secret, 1 Service",
                      messages)

    def test_cutover_scales_the_source_down_once_the_target_is_ready(self):
        self.ready_to_copy()
        source, target = self.kubes["source"], self.kubes["target"]
        target.lists["/apis/apps/v1/namespaces/shop/deployments"] = [
            {"metadata": {"name": "web"}, "spec": {"replicas": 2}, "status": {"readyReplicas": 0}}
        ]
        scale = "/apis/apps/v1/namespaces/shop/deployments/web/scale"
        source.responses[scale] = [{"spec": {"replicas": 2}, "status": {"replicas": 2}}, {"spec": {"replicas": 0}}]
        move = self.manifest_move(mode=Move.Mode.CUTOVER)

        self.advance_until_waiting(move)
        self.assertEqual((move.status, source.patches), (Move.Status.VERIFYING, []))

        target.lists["/apis/apps/v1/namespaces/shop/deployments"][0]["status"]["readyReplicas"] = 2
        self.advance_until_waiting(move)
        self.assertEqual((move.status, move.replicas), (Move.Status.SCALING_DOWN, {"shop/Deployment/web": 2}))
        self.assertEqual(source.patches, [(scale, {"spec": {"replicas": 0}})])

        self.assertIsNone(self.advance_until_waiting(move))
        self.assertEqual(move.status, Move.Status.DONE)

    def test_needs_no_velero(self):
        self.ready_to_copy()
        move = self.manifest_move()

        self.advance_until_waiting(move)

        self.assertEqual(move.status, Move.Status.DONE)
        self.assertNotIn("/apis/velero.io/v1", self.kubes["source"].responses)

    def test_refuses_volumes(self):
        self.ready_to_copy()
        self.kubes["source"].lists["/api/v1/namespaces/shop/persistentvolumeclaims"] = [
            {"metadata": {"name": "data", "namespace": "shop"}, "spec": {}}
        ]
        move = self.manifest_move()

        self.advance_until_waiting(move)

        self.assertEqual((move.status, move.failed_step), (Move.Status.FAILED, Move.Status.CHECKING))
        self.assertIn("have PersistentVolumeClaims: shop/data", move.error)
        self.assertEqual(self.kubes["target"].applies, [])

    def test_refuses_custom_resources(self):
        self.ready_to_copy()
        source = self.kubes["source"]
        serves(source, CORE_KINDS, custom=[("example.com", "widgets", "Widget", True)])
        source.lists["/apis/example.com/v1/namespaces/shop/widgets"] = [{"metadata": {"name": "w", "namespace": "shop"}}]
        move = self.manifest_move()

        self.advance_until_waiting(move)

        self.assertEqual(move.status, Move.Status.FAILED)
        self.assertIn("have custom resources: Widget shop/w", move.error)

    def test_unreadable_kind_fails_the_check(self):
        self.ready_to_copy()
        self.kubes["source"].lists["/api/v1/namespaces/shop/secrets"] = KubeAPIError("permission denied", 403)
        move = self.manifest_move()

        self.advance_until_waiting(move)

        self.assertEqual(move.status, Move.Status.FAILED)
        self.assertIn("CloudHop cannot read Secret objects in the source", move.error)

    def test_failed_apply_is_retried(self):
        self.ready_to_copy()
        target = self.kubes["target"]
        target.responses["apply /api/v1/namespaces/shop/secrets/db"] = KubeAPIError("permission denied", 403)
        move = self.manifest_move()

        self.advance_until_waiting(move)
        self.assertEqual((move.status, move.failed_step), (Move.Status.FAILED, Move.Status.COPYING))
        self.assertIn("Could not apply 1 object in the target: Secret shop/db: permission denied", move.error)

        del target.responses["apply /api/v1/namespaces/shop/secrets/db"]
        target.applies.clear()
        retry_move(move.pk)
        self.advance_until_waiting(move)

        self.assertEqual(move.status, Move.Status.DONE)
        self.assertEqual(len(target.applies), 5)


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

    def test_start_a_manifests_only_move(self):
        with mock.patch.object(advance_move, "delay"), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/v1/moves/", {
                "source_cluster": self.source_cluster.pk,
                "target_cluster": self.target_cluster.pk,
                "namespaces": ["shop"],
                "method": "manifests",
                "mode": "copy",
                "storage_class_mapping": {"standard": "fast"},
            }, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["method"], "manifests")
        self.assertEqual(response.data["steps"], ["checking", "copying", "verifying"])
        self.assertEqual(response.data["storage_class_mapping"], {})

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


class SetupKube(FakeKube):
    """A FakeKube that answers access reviews: allowed for the (verb, resource) pairs given."""

    def __init__(self, allowed=()):
        super().__init__()
        self.allowed = set(allowed)

    def post(self, path, body):
        if path != "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
            return super().post(path, body)
        attributes = body["spec"]["resourceAttributes"]
        resource = attributes["resource"] + (f"/{attributes['subresource']}" if attributes["subresource"] else "")
        return {"status": {"allowed": (attributes["verb"], resource) in self.allowed}}


VELERO_SOURCE_ACCESS = [("get", "deployments"), ("create", "backups"), ("get", "backupstoragelocations"), ("list", "volumesnapshotclasses"),
                        ("patch", "deployments/scale"), ("patch", "statefulsets/scale")]
VELERO_TARGET_ACCESS = [("get", "deployments"), ("create", "restores"), ("get", "backupstoragelocations"), ("create", "configmaps"),
                        ("patch", "deployments/scale"), ("patch", "statefulsets/scale")]


class MoveSetupTests(MoveTestCase):
    def setUp(self):
        super().setUp()
        self.kubes = {"source": SetupKube(VELERO_SOURCE_ACCESS), "target": SetupKube(VELERO_TARGET_ACCESS)}

        @contextmanager
        def fake_kube(cluster):
            yield self.kubes[cluster.name]

        patch = mock.patch("moves.services.setup._kube", fake_kube)
        patch.start()
        self.addCleanup(patch.stop)

        # The bucket, as the target's account reads it: a dict, None (no such bucket) or an error
        self.bucket = {"name": "cloudhop-moves", "location": "asia-southeast1", "in_account": True,
                       "owner": "target-project"}
        self.buckets_read = []

        def get_bucket(name):
            self.buckets_read.append(name)
            if isinstance(self.bucket, Exception):
                raise self.bucket
            return self.bucket

        # What Velero's identity lacks on the bucket's objects: a list or an error
        self.missing_access = []

        def missing_backup_access(name):
            if isinstance(self.missing_access, Exception):
                raise self.missing_access
            return self.missing_access

        identity = mock.Mock(return_value="cloudhop@target-project.iam.gserviceaccount.com")
        patch = mock.patch("moves.services.setup.BucketAdapter.for_account", return_value=mock.Mock(
            get_bucket=get_bucket, identity=identity, missing_backup_access=missing_backup_access,
            backup_role="roles/storage.objectAdmin",
        ))
        patch.start()
        self.addCleanup(patch.stop)

    def check(self, **params):
        response = self.client.get("/api/v1/moves/setup/", {
            "source_cluster": self.source_cluster.pk, "target_cluster": self.target_cluster.pk, **params,
        })
        self.assertEqual(response.status_code, 200, response.data)
        return response.data, {(c["key"], c["cluster"]): c for c in response.data["checks"]}

    def test_velero_ready_in_both_clusters(self):
        self.ready_to_check()

        data, checks = self.check(method="velero")

        self.assertEqual({c["status"] for c in data["checks"]}, {"ok"}, data["checks"])
        self.assertEqual(data["bucket"], "cloudhop-moves")
        self.assertIn("Both clusters use gs://cloudhop-moves", checks[("bucket", "")]["detail"])
        # Velero reuses the target account's service account
        self.assertEqual(data["identity"], "cloudhop@target-project.iam.gserviceaccount.com")
        self.assertEqual(set(checks), {
            ("snapshot_class", "source"), ("velero", "source"), ("storage_location", "source"), ("access", "source"),
            ("velero", "target"), ("storage_location", "target"), ("access", "target"), ("bucket", ""),
            ("bucket_access", ""),
        })

    def test_velero_missing_in_the_target(self):
        velero_ready(self.kubes["source"])

        data, checks = self.check(method="velero")

        self.assertEqual(checks[("velero", "target")]["status"], "missing")
        self.assertEqual(checks[("storage_location", "target")]["status"], "unknown")
        self.assertEqual(checks[("snapshot_class", "source")]["status"], "missing")
        # The bucket the source's Velero uses is checked, and given back for the target's commands
        self.assertEqual(checks[("bucket", "")]["status"], "ok")
        self.assertEqual(self.buckets_read, ["cloudhop-moves"])
        self.assertEqual(data["bucket"], "cloudhop-moves")

    def test_different_buckets_and_a_writable_target(self):
        velero_ready(self.kubes["source"], bucket="one")
        velero_ready(self.kubes["target"], bucket="two")

        _, checks = self.check(method="velero")

        self.assertEqual(checks[("bucket", "")]["status"], "missing")
        self.assertIn("gs://one", checks[("bucket", "")]["detail"])
        self.assertEqual(checks[("storage_location", "target")]["status"], "warning")

    def test_unavailable_storage_location(self):
        self.ready_to_check()
        self.kubes["target"].responses[f"{VELERO}/backupstoragelocations/default"]["status"]["phase"] = "Unavailable"

        _, checks = self.check(method="velero")

        self.assertEqual(checks[("storage_location", "target")]["status"], "missing")
        self.assertIn("Unavailable", checks[("storage_location", "target")]["detail"])

    def test_access_names_what_is_denied(self):
        self.ready_to_check()
        self.kubes["target"].allowed = {("get", "backupstoragelocations"), ("get", "deployments")}

        _, checks = self.check(method="velero")

        self.assertEqual(checks[("access", "source")]["status"], "ok")
        self.assertEqual(checks[("access", "target")]["status"], "missing")
        self.assertIn("create restores.velero.io in velero", checks[("access", "target")]["detail"])
        self.assertIn("patch deployments.apps/scale", checks[("access", "target")]["detail"])

    def test_manifests_copy_needs_no_scaling_or_velero(self):
        self.kubes["source"].allowed = {("list", "secrets")}
        self.kubes["target"].allowed = {("create", "namespaces"), ("patch", "namespaces"), ("patch", "secrets"),
                                        ("patch", "deployments")}

        data, checks = self.check(method="manifests", mode="copy")

        self.assertEqual(set(checks), {("access", "source"), ("access", "target")})
        self.assertEqual({c["status"] for c in data["checks"]}, {"ok"})
        self.assertIsNone(data["bucket"])
        self.assertIsNone(data["identity"])

    def test_manifests_cutover_scales_the_source(self):
        self.kubes["source"].allowed = {("list", "secrets")}

        _, checks = self.check(method="manifests", mode="cutover")

        self.assertIn("patch deployments.apps/scale", checks[("access", "source")]["detail"])

    def test_unreachable_cluster(self):
        @contextmanager
        def unreachable(cluster):
            raise CloudAPIError("permission denied")
            yield

        with mock.patch("moves.services.setup._kube", unreachable):
            _, checks = self.check(method="velero")

        self.assertEqual(checks[("connect", "source")]["status"], "missing")
        self.assertIn("permission denied", checks[("connect", "source")]["detail"])

    def test_same_cluster_is_refused(self):
        response = self.client.get("/api/v1/moves/setup/", {
            "source_cluster": self.source_cluster.pk, "target_cluster": self.source_cluster.pk,
        })

        self.assertEqual(response.status_code, 400)

    def test_velero_install_problems(self):
        self.ready_to_check()
        velero_ready(self.kubes["source"], features="", plugin="v1.12.0")

        _, checks = self.check(method="velero")

        velero = checks[("velero", "source")]
        self.assertEqual(velero["status"], "missing")
        self.assertIn("Velero v1.18.4 is installed, but its CSI support is off", velero["detail"])
        self.assertIn("it needs v1.14.x", velero["detail"])
        self.assertEqual(checks[("velero", "target")]["status"], "ok")
        self.assertIn("with CSI support and GCP plugin v1.14.4", checks[("velero", "target")]["detail"])

    def test_velero_versions_differ(self):
        self.ready_to_check()
        velero_ready(self.kubes["target"], read_only=True, version="v1.17.1", plugin="v1.13.2")

        _, checks = self.check(method="velero")

        self.assertEqual(checks[("velero", "target")]["status"], "warning")
        self.assertIn("The source runs Velero v1.18.4", checks[("velero", "target")]["detail"])

    def test_bucket_named_before_velero_is_installed(self):
        data, checks = self.check(method="velero", bucket="my-bucket")

        self.assertEqual(self.buckets_read, ["my-bucket"])
        self.assertEqual(checks[("bucket", "")]["status"], "ok")
        self.assertIn("exists in target-project", checks[("bucket", "")]["detail"])
        self.assertEqual(data["bucket"], "my-bucket")

    def test_bucket_not_created_yet(self):
        self.bucket = None

        _, checks = self.check(method="velero", bucket="my-bucket")

        self.assertEqual(checks[("bucket", "")]["status"], "missing")
        self.assertIn("no bucket gs://my-bucket", checks[("bucket", "")]["detail"])
        self.assertEqual(checks[("bucket_access", "")]["status"], "unknown")

    def test_bucket_cloudhop_may_not_read(self):
        self.bucket = CloudAPIError("gs://my-bucket exists, but cloudhop@x may not read it. Grant it: ...", 403)

        _, checks = self.check(method="velero", bucket="my-bucket")

        self.assertEqual(checks[("bucket", "")]["status"], "unknown")
        self.assertTrue(checks[("bucket", "")]["detail"].startswith("gs://my-bucket exists, but"))
        # Velero's access is asked for all the same
        self.assertEqual(checks[("bucket_access", "")]["status"], "ok")

    def test_bucket_in_another_project(self):
        self.bucket = {**self.bucket, "in_account": False, "owner": "project number 42"}

        _, checks = self.check(method="velero", bucket="my-bucket")

        self.assertEqual(checks[("bucket", "")]["status"], "warning")
        self.assertIn("project number 42", checks[("bucket", "")]["detail"])

    def test_velero_may_not_write_backups(self):
        self.missing_access = ["storage.objects.create", "storage.objects.delete"]

        _, checks = self.check(method="velero", bucket="my-bucket")

        self.assertEqual(checks[("bucket", "")]["status"], "ok")
        self.assertEqual(checks[("bucket_access", "")]["status"], "missing")
        detail = checks[("bucket_access", "")]["detail"]
        self.assertIn("cloudhop@target-project.iam.gserviceaccount.com", detail)
        self.assertIn("storage.objects.create, storage.objects.delete", detail)
        self.assertIn("roles/storage.objectAdmin", detail)

    def test_velero_access_cannot_be_asked(self):
        self.missing_access = CloudAPIError("cannot reach GCP, try again later")

        _, checks = self.check(method="velero", bucket="my-bucket")

        self.assertEqual(checks[("bucket_access", "")]["status"], "unknown")

    def test_no_bucket_name(self):
        data, checks = self.check(method="velero")

        self.assertEqual(checks[("bucket", "")]["status"], "unknown")
        self.assertEqual(self.buckets_read, [])
        self.assertIsNone(data["bucket"])

    def test_invalid_bucket_name_is_refused(self):
        response = self.client.get("/api/v1/moves/setup/", {
            "source_cluster": self.source_cluster.pk, "target_cluster": self.target_cluster.pk, "bucket": "Not/A{bucket}",
        })

        self.assertEqual(response.status_code, 400)


class GCPBucketAdapterTests(MoveTestCase):
    def adapter(self):
        adapter = GCPBucketAdapter(self.target_cluster.account_id)
        adapter.credential = {"client_email": "cloudhop@target-project.iam.gserviceaccount.com"}
        return adapter

    def get(self, *answers):
        return mock.patch("moves.adapters.gcp.GCPClient.get", side_effect=answers)

    def test_bucket_in_the_account(self):
        with self.get({"name": "b", "location": "ASIA-SOUTHEAST1", "projectNumber": "123"},
                      {"name": "projects/123"}) as get:
            bucket = self.adapter().get_bucket("b")

        self.assertEqual(bucket, {"name": "b", "location": "asia-southeast1", "in_account": True,
                                  "owner": "target-project"})
        self.assertIn("/storage/v1/b/b?", get.call_args_list[0].args[0])

    def test_bucket_in_another_project(self):
        with self.get({"name": "b", "projectNumber": "999"}, {"name": "projects/123"}):
            bucket = self.adapter().get_bucket("b")

        self.assertFalse(bucket["in_account"])
        self.assertEqual(bucket["owner"], "project number 999")

    def test_no_such_bucket(self):
        with self.get(CloudAPIError("not found", 404)):
            self.assertIsNone(self.adapter().get_bucket("b"))

    def test_bucket_that_may_not_be_read_says_what_to_grant(self):
        with self.get(CloudAPIError("permission denied", 403)), self.assertRaises(CloudAPIError) as caught:
            self.adapter().get_bucket("b")

        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("--member serviceAccount:cloudhop@target-project.iam.gserviceaccount.com",
                      str(caught.exception))
        self.assertIn("roles/storage.legacyBucketReader", str(caught.exception))

    def test_missing_backup_access(self):
        with self.get({"permissions": ["storage.objects.get", "storage.objects.list"]}) as get:
            missing = self.adapter().missing_backup_access("b")

        self.assertEqual(missing, ["storage.objects.create", "storage.objects.delete"])
        self.assertIn("/storage/v1/b/b/iam/testPermissions?permissions=storage.objects.create", get.call_args.args[0])

    def test_all_backup_access(self):
        with self.get({"permissions": ["storage.objects.create", "storage.objects.get", "storage.objects.list",
                                       "storage.objects.delete"]}):
            self.assertEqual(self.adapter().missing_backup_access("b"), [])

    def test_no_backup_access(self):
        # Cloud Storage leaves the list out when the caller has none of the permissions
        with self.get({}):
            self.assertEqual(len(self.adapter().missing_backup_access("b")), 4)

    def test_other_failures_pass_through(self):
        with self.get(CloudAPIError("GCP returned 500", 500)), self.assertRaises(CloudAPIError) as caught:
            self.adapter().get_bucket("b")

        self.assertEqual(caught.exception.status_code, 500)


class VeleroInstallTests(APITestCase):
    def test_read_velero(self):
        kube = FakeKube()
        velero_ready(kube, version="v1.18.4", plugin="v1.14.4", features="EnableCSI,EnableAPIGroupVersions")

        self.assertEqual(read_velero(kube), {
            "version": "v1.18.4",
            "features": {"EnableCSI", "EnableAPIGroupVersions"},
            "plugins": {"velero/velero-plugin-for-gcp": "v1.14.4"},
        })
        self.assertIsNone(read_velero(FakeKube()))

    def test_matching_plugins(self):
        for version, plugin in (("v1.18.4", "v1.14.4"), ("v1.16.0", "v1.12.0"), ("v1.14.1", "v1.10.2")):
            velero = {"version": version, "features": {"EnableCSI"}, "plugins": {"velero/velero-plugin-for-gcp": plugin}}
            self.assertEqual(velero_problems(velero), [], (version, plugin))

    def test_problems(self):
        velero = {"version": "v1.18.4", "features": set(), "plugins": {}}

        problems = velero_problems(velero)

        self.assertEqual(len(problems), 2)
        self.assertIn("CSI support is off", problems[0])
        self.assertIn("no GCP plugin", problems[1])
        self.assertEqual(len(velero_problems(velero, volumes=False)), 1)
