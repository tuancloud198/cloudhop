import logging
import re
from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from clusters.models.cluster import Clusters
from common.cloud.errors import CloudAPIError
from common.kube.client import KubeAPIError, KubeClient
from kubernetes.adapters.base import KubeAccessAdapter
from moves.models.move import Move
from moves.models.move_event import MoveEvent


logger = logging.getLogger(__name__)

Status = Move.Status

# Statuses of a move that is still going; PENDING has not run its first step yet
ACTIVE = (
    Status.PENDING, Status.CHECKING, Status.SCALING_DOWN, Status.BACKING_UP,
    Status.RESTORING, Status.SCALING_UP, Status.VERIFYING,
)
FINISHED = (Status.DONE, Status.FAILED, Status.CANCELLED)

# Where Velero is installed, in both clusters
VELERO_NAMESPACE = "velero"
VELERO = f"/apis/velero.io/v1/namespaces/{VELERO_NAMESPACE}"
# Velero keeps a move's backup this long, so a failed restore can be retried
BACKUP_TTL = "168h0m0s"
# Velero's restore plugin reads storage class renames from this ConfigMap
STORAGE_CLASS_CONFIG = "change-storage-class-config"

# Seconds between two runs of a step that is waiting
POLL_SECONDS = 15
# How long a step may wait before the move fails
STEP_TIMEOUTS = {
    Status.SCALING_DOWN: timedelta(minutes=10),
    # Includes uploading every volume
    Status.BACKING_UP: timedelta(hours=6),
    # Includes the target's Velero noticing the backup and downloading every volume
    Status.RESTORING: timedelta(hours=6),
    Status.VERIFYING: timedelta(minutes=20),
}

# Velero Backup and Restore phases that end them without success
FAILED_PHASES = {"Failed", "FailedValidation", "PartiallyFailed"}
# Workloads a cutover scales down, and up again in the target
WORKLOADS = (("Deployment", "deployments"), ("StatefulSet", "statefulsets"))
DNS_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")

DONE, WAIT = "done", "wait"


class InvalidMove(Exception):
    """The move cannot be started as asked; the message says why and is safe to show to the user."""


class MoveError(Exception):
    """A step cannot go on; the move fails with this message, which is safe to show to the user."""


def steps_of(mode: str) -> list[str]:
    """The statuses a move in this mode goes through, in order."""
    if mode == Move.Mode.CUTOVER:
        return [Status.CHECKING, Status.SCALING_DOWN, Status.BACKING_UP, Status.RESTORING,
                Status.SCALING_UP, Status.VERIFYING]
    return [Status.CHECKING, Status.BACKING_UP, Status.RESTORING, Status.VERIFYING]


def create_move(
    source_cluster: Clusters,
    target_cluster: Clusters,
    namespaces: list[str],
    mode: str = Move.Mode.CUTOVER,
    storage_class_mapping: dict | None = None,
    storage_location: str = "default",
) -> Move:
    """Store a move ready to run; queue moves.tasks.advance_move to start it.

    Raises InvalidMove when the clusters or namespaces cannot be moved.
    """
    if source_cluster.pk == target_cluster.pk:
        raise InvalidMove("the source and target must be different clusters")
    for side, cluster in (("source", source_cluster), ("target", target_cluster)):
        account = cluster.account_id
        if not cluster.is_active:
            raise InvalidMove(f"the {side} cluster {cluster.name} is inactive")
        if not account.is_active or not account.is_valid:
            raise InvalidMove(f"the {side} cluster's account {account.name} is inactive or not validated")

    namespaces = sorted(set(namespaces))
    if not namespaces:
        raise InvalidMove("choose at least one namespace")
    adapter = KubeAccessAdapter.for_account(source_cluster.account_id)
    for namespace in namespaces:
        if not DNS_LABEL.fullmatch(namespace):
            raise InvalidMove(f"{namespace!r} is not a valid namespace name")
        if adapter.is_system_namespace(namespace) or namespace in ("default", VELERO_NAMESPACE):
            raise InvalidMove(f"namespace {namespace} belongs to the cluster or to Velero and cannot be moved")

    clusters = [source_cluster, target_cluster]
    busy = Move.objects.filter(Q(source_cluster__in=clusters) | Q(target_cluster__in=clusters), status__in=ACTIVE).first()
    if busy:
        raise InvalidMove(f"move {busy.pk} is still running on one of these clusters")

    move = Move.objects.create(
        source_cluster=source_cluster,
        target_cluster=target_cluster,
        namespaces=namespaces,
        mode=mode,
        storage_class_mapping={k: v for k, v in (storage_class_mapping or {}).items() if k != v},
        storage_location=storage_location,
    )
    _log(move, f"Created: {', '.join(namespaces)} from {source_cluster.name} to {target_cluster.name}, {mode} mode")
    return move


def advance(move_id: int) -> int | None:
    """Run the move's current step once, and go to the next step when it is done.

    Returns the seconds after which to run it again, or None when the move is finished
    or another worker is running it.
    """
    with transaction.atomic():
        # A second task for the same move gives way instead of running the step twice
        move = Move.objects.select_for_update(skip_locked=True).filter(pk=move_id).first()
        if move is None or move.status in FINISHED:
            return None
        if move.status == Status.PENDING:
            _enter(move, Status.CHECKING)

        try:
            outcome = STEPS[move.status](move)
        except (MoveError, CloudAPIError) as exc:
            _fail(move, str(exc))
            return None

        move.checked_at = timezone.now()
        if outcome == WAIT:
            timeout = STEP_TIMEOUTS.get(move.status)
            if timeout and move.step_started_at and move.checked_at - move.step_started_at > timeout:
                _fail(move, f"{move.get_status_display()} did not finish within {_duration(timeout)}: {move.waiting_on}")
                return None
            move.save()
            return POLL_SECONDS

        steps = steps_of(move.mode)
        following = steps[steps.index(move.status) + 1:]
        if not following:
            move.status, move.waiting_on, move.finished_at = Status.DONE, "", move.checked_at
            move.save()
            _log(move, "Done")
            return None
        _enter(move, following[0])
        return 0


def cancel_move(move_id: int) -> Move:
    """Stop a move after its current step. Nothing it did is undone.

    Raises Move.DoesNotExist, or InvalidMove when the move already finished.
    """
    with transaction.atomic():
        move = Move.objects.select_for_update().get(pk=move_id)
        if move.status not in ACTIVE:
            raise InvalidMove(f"move {move_id} is {move.status}, not running")
        step = move.status
        move.status, move.waiting_on, move.finished_at = Status.CANCELLED, "", timezone.now()
        move.save()
        left = "The source workloads stay scaled down; scale them up to use them again. " if move.replicas else ""
        _log(move, f"Cancelled during {Status(step).label.lower()}. {left}Velero backups or restores already "
                   f"started keep running.", step=step)
    return move


def retry_move(move_id: int) -> Move:
    """Run a failed move again from the step it failed at; queue moves.tasks.advance_move.

    Raises Move.DoesNotExist, or InvalidMove when the move did not fail.
    """
    with transaction.atomic():
        move = Move.objects.select_for_update().get(pk=move_id)
        if move.status != Status.FAILED:
            raise InvalidMove(f"move {move_id} is {move.status}; only a failed move can be retried")
        step = move.failed_step or Status.CHECKING
        if step in (Status.BACKING_UP, Status.RESTORING):
            # A Velero object of the same name already ended; the retry needs a new one
            move.attempt += 1
            move.restore_name = ""
            if step == Status.BACKING_UP:
                move.backup_name = ""
        move.status, move.error, move.failed_step, move.finished_at = step, "", "", None
        move.step_started_at, move.waiting_on = timezone.now(), ""
        move.save()
        _log(move, f"Retrying from {Status(step).label.lower()}")
    return move


# Steps: each runs once per call and returns DONE or WAIT (after setting move.waiting_on).
# They check what is already done first, so running one again is safe.

def _check(move: Move) -> str:
    """Both clusters can take part: Velero ready on one bucket, namespaces free, storage classes known."""
    with _kube(move.source_cluster) as source, _kube(move.target_cluster) as target:
        locations = {}
        for side, kube in (("source", source), ("target", target)):
            if not _exists(kube, "/apis/velero.io/v1"):
                raise MoveError(f"Velero is not installed in the {side} cluster. See the Guide, under Moves.")
            locations[side] = _storage_location(kube, side, move.storage_location)

        buckets = {side: (loc["spec"].get("objectStorage") or {}) for side, loc in locations.items()}
        if (buckets["source"].get("bucket"), buckets["source"].get("prefix")) != (
            buckets["target"].get("bucket"), buckets["target"].get("prefix")
        ):
            raise MoveError(
                f"Velero's storage location {move.storage_location} uses bucket "
                f"{buckets['source'].get('bucket')} in the source and {buckets['target'].get('bucket')} in the "
                f"target; both must use the same bucket and prefix."
            )
        if locations["target"]["spec"].get("accessMode") != "ReadOnly":
            _log(move, "The target's storage location is not read-only, so its Velero could also write to the "
                       "bucket. Setting accessMode: ReadOnly is safer.", level=MoveEvent.Level.WARNING)

        classes, claims = set(), 0
        for namespace in move.namespaces:
            if not _exists(source, f"/api/v1/namespaces/{namespace}"):
                raise MoveError(f"Namespace {namespace} does not exist in the source cluster.")
            if _exists(target, f"/api/v1/namespaces/{namespace}"):
                raise MoveError(
                    f"Namespace {namespace} already exists in the target cluster. Velero leaves objects that "
                    f"already exist alone, so delete it there first, or leave it out of the move."
                )
            for claim in source.list(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims"):
                classes.add(claim["spec"].get("storageClassName") or "")
                claims += 1

        missing = sorted(
            f"{name} (as {move.storage_class_mapping[name]})" if name in move.storage_class_mapping else name
            for name in classes - {""}
            if not _exists(target, f"/apis/storage.k8s.io/v1/storageclasses/{move.storage_class_mapping.get(name, name)}")
        )
        if missing:
            raise MoveError(
                f"The target cluster has no storage class {', '.join(missing)}. Map each to one the target has."
            )
        if classes and not any(
            (item["metadata"].get("labels") or {}).get("velero.io/csi-volumesnapshot-class") == "true"
            for item in _list_or_empty(source, "/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses")
        ):
            raise MoveError(
                "The source cluster has no VolumeSnapshotClass labelled velero.io/csi-volumesnapshot-class=true, "
                "which Velero needs to copy volumes. See the Guide, under Moves."
            )

    _log(move, f"Checked: Velero is ready in both clusters on bucket {buckets['source'].get('bucket')}; "
               f"{claims} volume{'s' if claims != 1 else ''} to copy")
    return DONE


def _scale_down(move: Move) -> str:
    """Record the source workloads' replicas, scale them to 0 and wait for their pods to stop."""
    with _kube(move.source_cluster) as kube:
        if not move.replicas:
            replicas = {}
            for namespace in move.namespaces:
                for kind, resource in WORKLOADS:
                    for item in kube.list(f"/apis/apps/v1/namespaces/{namespace}/{resource}"):
                        replicas[f"{namespace}/{kind}/{item['metadata']['name']}"] = item["spec"].get("replicas", 1)
            # Saved before scaling, so a retry never records the scaled-down 0s
            move.replicas = replicas
            move.save(update_fields=["replicas", "updated_at"])
            running = sum(1 for count in replicas.values() if count)
            _log(move, f"Scaling down {running} workload{'s' if running != 1 else ''} in the source")

        stopping = []
        for key, count in move.replicas.items():
            if not count:
                continue
            scale = _get_or_none(kube, _scale_path(key))
            if scale is None:
                continue
            if scale["spec"].get("replicas"):
                kube.patch(_scale_path(key), {"spec": {"replicas": 0}})
            if scale.get("status", {}).get("replicas"):
                stopping.append(key)

    if stopping:
        move.waiting_on = f"Pods still running: {', '.join(stopping[:5])}{' …' if len(stopping) > 5 else ''}"
        return WAIT
    return DONE


def _back_up(move: Move) -> str:
    """Back up every object and volume of the namespaces into the bucket."""
    with _kube(move.source_cluster) as kube:
        if not move.backup_name:
            name = f"cloudhop-move-{move.pk}-{move.attempt}"
            _create(kube, f"{VELERO}/backups", {
                "apiVersion": "velero.io/v1",
                "kind": "Backup",
                "metadata": {"name": name, "namespace": VELERO_NAMESPACE, "labels": {"cloudhop.io/move": str(move.pk)}},
                "spec": {
                    "includedNamespaces": move.namespaces,
                    "storageLocation": move.storage_location,
                    # Copies volume data into the bucket instead of leaving it in provider snapshots
                    "snapshotMoveData": True,
                    "ttl": BACKUP_TTL,
                },
            })
            move.backup_name = name
            move.save(update_fields=["backup_name", "updated_at"])
            _log(move, f"Started Velero backup {name} in the source")

        backup = kube.get(f"{VELERO}/backups/{move.backup_name}")

    status = backup.get("status") or {}
    phase = status.get("phase") or "New"
    if phase == "Completed":
        progress = status.get("progress") or {}
        _log(move, f"Backup {move.backup_name} completed: {progress.get('itemsBackedUp', '?')} objects")
        return DONE
    if phase in FAILED_PHASES:
        reason = status.get("failureReason") or "; ".join(status.get("validationErrors") or []) or "see its logs"
        raise MoveError(
            f"Velero backup {move.backup_name} ended {phase}: {reason}. "
            f"For details run in the source cluster: velero backup logs {move.backup_name}"
        )
    move.waiting_on = f"Backup {phase}{_progress(status.get('progress'))}"
    return WAIT


def _restore(move: Move) -> str:
    """Restore the backup into the target, once the target's Velero has found it in the bucket."""
    with _kube(move.target_cluster) as kube:
        if not move.restore_name:
            backup = _get_or_none(kube, f"{VELERO}/backups/{move.backup_name}")
            if backup is None or (backup.get("status") or {}).get("phase") != "Completed":
                # Velero lists the bucket's backups every minute by default
                move.waiting_on = f"The target's Velero has not found backup {move.backup_name} in the bucket yet"
                return WAIT

            if move.storage_class_mapping:
                _write_storage_class_config(kube, move.storage_class_mapping)
            name = f"cloudhop-move-{move.pk}-{move.attempt}"
            _create(kube, f"{VELERO}/restores", {
                "apiVersion": "velero.io/v1",
                "kind": "Restore",
                "metadata": {"name": name, "namespace": VELERO_NAMESPACE, "labels": {"cloudhop.io/move": str(move.pk)}},
                "spec": {
                    "backupName": move.backup_name,
                    "includedNamespaces": move.namespaces,
                    "restorePVs": True,
                    "existingResourcePolicy": "none",
                },
            })
            move.restore_name = name
            move.save(update_fields=["restore_name", "updated_at"])
            _log(move, f"Started Velero restore {name} in the target")

        restore = kube.get(f"{VELERO}/restores/{move.restore_name}")

    status = restore.get("status") or {}
    phase = status.get("phase") or "New"
    if phase == "Completed":
        if status.get("warnings"):
            _log(move, f"Restore {move.restore_name} completed with {status['warnings']} warnings. To see them run "
                       f"in the target cluster: velero restore describe {move.restore_name}",
                 level=MoveEvent.Level.WARNING)
        else:
            _log(move, f"Restore {move.restore_name} completed")
        return DONE
    if phase in FAILED_PHASES:
        reason = status.get("failureReason") or "; ".join(status.get("validationErrors") or []) or (
            f"{status.get('errors', '?')} errors"
        )
        raise MoveError(
            f"Velero restore {move.restore_name} ended {phase}: {reason}. "
            f"For details run in the target cluster: velero restore logs {move.restore_name}"
        )
    move.waiting_on = f"Restore {phase}{_progress(status.get('progress'))}"
    return WAIT


def _scale_up(move: Move) -> str:
    """Give the restored workloads the replicas the source had before the cutover."""
    with _kube(move.target_cluster) as kube:
        for key, count in move.replicas.items():
            if not count:
                continue
            scale = _get_or_none(kube, _scale_path(key))
            if scale is None:
                _log(move, f"{key} was not restored in the target", level=MoveEvent.Level.WARNING)
            elif scale["spec"].get("replicas") != count:
                kube.patch(_scale_path(key), {"spec": {"replicas": count}})
    running = sum(1 for count in move.replicas.values() if count)
    _log(move, f"Scaled up {running} workload{'s' if running != 1 else ''} in the target")
    return DONE


def _verify(move: Move) -> str:
    """Wait until every workload in the target is ready and every volume bound."""
    pending = []
    with _kube(move.target_cluster) as kube:
        for namespace in move.namespaces:
            for kind, resource in WORKLOADS:
                for item in kube.list(f"/apis/apps/v1/namespaces/{namespace}/{resource}"):
                    wanted = item["spec"].get("replicas", 1)
                    ready = (item.get("status") or {}).get("readyReplicas") or 0
                    if ready < wanted:
                        pending.append(f"{kind} {namespace}/{item['metadata']['name']} {ready}/{wanted} ready")
            for claim in kube.list(f"/api/v1/namespaces/{namespace}/persistentvolumeclaims"):
                phase = (claim.get("status") or {}).get("phase")
                if phase != "Bound":
                    pending.append(f"PVC {namespace}/{claim['metadata']['name']} {phase or 'Pending'}")
    if pending:
        move.waiting_on = "; ".join(pending[:5]) + (" …" if len(pending) > 5 else "")
        return WAIT
    _log(move, "Every workload is ready and every volume bound in the target")
    return DONE


STEPS = {
    Status.CHECKING: _check,
    Status.SCALING_DOWN: _scale_down,
    Status.BACKING_UP: _back_up,
    Status.RESTORING: _restore,
    Status.SCALING_UP: _scale_up,
    Status.VERIFYING: _verify,
}


@contextmanager
def _kube(cluster: Clusters):
    with KubeAccessAdapter.for_account(cluster.account_id).connect(cluster) as kube:
        yield kube


def _exists(kube: KubeClient, path: str) -> bool:
    return _get_or_none(kube, path) is not None


def _get_or_none(kube: KubeClient, path: str) -> dict | None:
    try:
        return kube.get(path)
    except KubeAPIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _list_or_empty(kube: KubeClient, path: str) -> list[dict]:
    """List a path whose API may not be installed (404)."""
    try:
        return kube.list(path)
    except KubeAPIError as exc:
        if exc.status_code == 404:
            return []
        raise


def _create(kube: KubeClient, path: str, body: dict) -> None:
    """Create the object; one of that name already made by an earlier run of the step is fine."""
    try:
        kube.post(path, body)
    except KubeAPIError as exc:
        if exc.status_code != 409:
            raise


def _storage_location(kube: KubeClient, side: str, name: str) -> dict:
    location = _get_or_none(kube, f"{VELERO}/backupstoragelocations/{name}")
    if location is None:
        raise MoveError(f"Velero in the {side} cluster has no storage location named {name}.")
    phase = (location.get("status") or {}).get("phase")
    if phase != "Available":
        raise MoveError(
            f"Velero's storage location {name} in the {side} cluster is {phase or 'not checked yet'}, not Available. "
            f"Check that Velero can reach the bucket: velero backup-location get"
        )
    return location


def _write_storage_class_config(kube: KubeClient, mapping: dict) -> None:
    """Tell Velero's restore to rename storage classes; merged into what other moves set."""
    path = f"/api/v1/namespaces/{VELERO_NAMESPACE}/configmaps"
    try:
        kube.post(path, {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": STORAGE_CLASS_CONFIG,
                "labels": {"velero.io/plugin-config": "", "velero.io/change-storage-class": "RestoreItemAction"},
            },
            "data": mapping,
        })
    except KubeAPIError as exc:
        if exc.status_code != 409:
            raise
        kube.patch(f"{path}/{STORAGE_CLASS_CONFIG}", {"data": mapping})


def _scale_path(key: str) -> str:
    namespace, kind, name = key.split("/")
    resource = dict(WORKLOADS)[kind]
    return f"/apis/apps/v1/namespaces/{namespace}/{resource}/{name}/scale"


def _progress(progress: dict | None) -> str:
    if not progress or not progress.get("totalItems"):
        return ""
    done = progress.get("itemsBackedUp", progress.get("itemsRestored", 0))
    return f": {done} of {progress['totalItems']} objects"


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} hours" if minutes >= 120 else f"{minutes} minutes"


def _enter(move: Move, status: str) -> None:
    move.status, move.waiting_on, move.step_started_at = status, "", timezone.now()
    move.save()


def _fail(move: Move, message: str) -> None:
    logger.warning("Move %s failed during %s: %s", move.pk, move.status, message)
    step = move.status
    move.status, move.failed_step, move.error = Status.FAILED, step, message
    move.waiting_on, move.finished_at = "", timezone.now()
    move.save()
    _log(move, message, level=MoveEvent.Level.ERROR, step=step)


def _log(move: Move, message: str, level: str = MoveEvent.Level.INFO, step: str | None = None) -> None:
    MoveEvent.objects.create(move=move, step=step or move.status, level=level, message=message)
