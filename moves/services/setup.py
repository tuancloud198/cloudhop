import re
from contextlib import contextmanager

from clusters.models.cluster import Clusters
from common.cloud.errors import CloudAPIError
from common.kube.client import KubeAPIError, KubeClient
from kubernetes.adapters.base import KubeAccessAdapter
from moves.adapters.base import BucketAdapter
from moves.models.move import Move
from moves.services.move import GCP_PLUGIN, VELERO, VELERO_NAMESPACE, read_velero, velero_problems

# What a check found: done, still to do, done but not as advised, or CloudHop cannot tell
OK, MISSING, WARNING, UNKNOWN = "ok", "missing", "warning", "unknown"

ACCESS_REVIEW = "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"
# How often the target's Velero should look for new backups in the bucket; a move's restore waits
# for it to find the backup. Velero's own default is a minute.
SYNC_PERIOD = 10
DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(h|ms|m|s)")
SNAPSHOT_CLASS_LABEL = "velero.io/csi-volumesnapshot-class"

# What CloudHop does in each cluster during a move, as (verb, group, resource[/subresource], namespace)
SCALE = [("patch", "apps", "deployments/scale", ""), ("patch", "apps", "statefulsets/scale", "")]
NEEDS = {
    (Move.Method.VELERO, "source"): [
        ("create", "velero.io", "backups", VELERO_NAMESPACE),
        ("get", "apps", "deployments", VELERO_NAMESPACE),
        ("list", "velero.io", "datauploads", VELERO_NAMESPACE),
        ("list", "", "events", VELERO_NAMESPACE),
        ("get", "velero.io", "backupstoragelocations", VELERO_NAMESPACE),
        ("list", "snapshot.storage.k8s.io", "volumesnapshotclasses", ""),
    ],
    (Move.Method.VELERO, "target"): [
        ("create", "velero.io", "restores", VELERO_NAMESPACE),
        ("get", "apps", "deployments", VELERO_NAMESPACE),
        ("list", "velero.io", "datadownloads", VELERO_NAMESPACE),
        ("list", "", "events", VELERO_NAMESPACE),
        ("get", "velero.io", "backupstoragelocations", VELERO_NAMESPACE),
        ("create", "", "configmaps", VELERO_NAMESPACE),
    ],
    # Every object is read, Secrets included
    (Move.Method.MANIFESTS, "source"): [("list", "", "secrets", "")],
    # Objects are server-side applied, which is a patch
    (Move.Method.MANIFESTS, "target"): [
        ("create", "", "namespaces", ""),
        ("patch", "", "namespaces", ""),
        ("patch", "", "secrets", ""),
        ("patch", "apps", "deployments", ""),
    ],
}


def check_setup(
    source_cluster: Clusters,
    target_cluster: Clusters,
    method: str,
    mode: str,
    storage_location: str = "default",
    bucket: str = "",
) -> dict:
    """What a move between these clusters needs set up, and whether it is, without starting one.

    bucket is the name the user means to give the bucket; the one Velero already uses wins over it.
    Returns {"checks": [{key, cluster, status, detail}], "bucket": the bucket checked, or None,
    "identity": the target account's service account, which Velero reuses, or None}.
    Keys: connect, velero, storage_location, snapshot_class, access (per cluster); bucket, bucket_access (both).
    The move's own check step stays the authority; this one does not look at namespaces.
    """
    checks, locations, versions = [], {}, {}
    for side, cluster in (("source", source_cluster), ("target", target_cluster)):
        try:
            with _kube(cluster) as kube:
                if method == Move.Method.VELERO:
                    locations[side] = _check_velero(kube, side, storage_location, checks, versions)
                _check_access(kube, side, _needs(method, mode, side), checks)
        except CloudAPIError as exc:
            _add(checks, "connect", side, MISSING, f"CloudHop cannot reach the {side} cluster {cluster.name}: {exc}")

    if len(set(versions.values())) > 1:
        target = next(check for check in checks if check["key"] == "velero" and check["cluster"] == "target")
        if target["status"] == OK:
            target["status"] = WARNING
        target["detail"] += (f" The source runs Velero {versions['source']}; install the same version in both, so the "
                             f"target restores what the source backs up.")

    identity = None
    if method == Move.Method.VELERO:
        bucket = _check_bucket(target_cluster.account_id, locations, bucket, checks)
        try:
            identity = BucketAdapter.for_account(target_cluster.account_id).identity()
        except (CloudAPIError, KeyError):
            # No key to read; the commands keep a placeholder
            pass
    return {"checks": checks, "bucket": bucket or None, "identity": identity}


@contextmanager
def _kube(cluster: Clusters):
    with KubeAccessAdapter.for_account(cluster.account_id).connect(cluster) as kube:
        yield kube


def _needs(method: str, mode: str, side: str) -> list[tuple]:
    needs = list(NEEDS[(method, side)])
    # Velero cutovers scale the source down and the target up; manifests ones the source only
    if mode == Move.Mode.CUTOVER and (method == Move.Method.VELERO or side == "source"):
        needs += SCALE
    return needs


def _check_velero(kube: KubeClient, side: str, name: str, checks: list, versions: dict) -> dict | None:
    """Velero installed with what moves need, its storage location Available, and in the source a
    snapshot class. Records the side's Velero version in versions.

    Returns the storage location's objectStorage, for comparing buckets, or None if there is none.
    """
    if side == "source":
        _check_snapshot_class(kube, checks)

    try:
        installed = _get(kube, "/apis/velero.io/v1") is not None
    except KubeAPIError as exc:
        _add(checks, "velero", side, UNKNOWN, f"CloudHop cannot tell whether Velero is installed: {exc}")
        _add(checks, "storage_location", side, UNKNOWN, "Install Velero first.")
        return None
    if not installed:
        _add(checks, "velero", side, MISSING, "Velero is not installed.")
        _add(checks, "storage_location", side, UNKNOWN, "Install Velero first.")
        return None
    _check_velero_install(kube, side, checks, versions)

    try:
        location = _get(kube, f"{VELERO}/backupstoragelocations/{name}")
    except KubeAPIError as exc:
        _add(checks, "storage_location", side, UNKNOWN, f"CloudHop cannot read storage location {name}: {exc}")
        return None
    if location is None:
        _add(checks, "storage_location", side, MISSING, f"Velero has no storage location named {name}.")
        return None

    storage = location["spec"].get("objectStorage") or {}
    where = _gs_url(storage)
    phase = (location.get("status") or {}).get("phase")
    if phase != "Available":
        _add(checks, "storage_location", side, MISSING,
             f"Storage location {name} on {where} is {phase or 'not checked yet'}, not Available: "
             f"Velero cannot reach the bucket. Run velero backup-location get.")
    elif side == "target" and location["spec"].get("accessMode") != "ReadOnly":
        _add(checks, "storage_location", side, WARNING,
             f"Storage location {name} is Available on {where}, but not read-only, so the target's Velero "
             f"could also write to the bucket.")
    elif side == "target" and _seconds(location["spec"].get("backupSyncPeriod")) > SYNC_PERIOD:
        period = location["spec"].get("backupSyncPeriod") or "1m (Velero's default)"
        _add(checks, "storage_location", side, WARNING,
             f"Storage location {name} is Available on {where} (read-only), but looks for new backups every "
             f"{period}, so a move waits that long to restore. Set backupSyncPeriod to {SYNC_PERIOD}s.")
    else:
        read_only = " (read-only)" if side == "target" else ""
        _add(checks, "storage_location", side, OK, f"Storage location {name} is Available on {where}{read_only}.")
    return storage


def _check_velero_install(kube: KubeClient, side: str, checks: list, versions: dict) -> None:
    """Velero's version, CSI support and GCP plugin, as `velero install` set them up."""
    try:
        velero = read_velero(kube)
    except KubeAPIError as exc:
        _add(checks, "velero", side, UNKNOWN, f"Velero is installed, but CloudHop cannot read how: {exc}")
        return
    if velero is None:
        _add(checks, "velero", side, WARNING,
             "Velero is installed, but not as the velero Deployment `velero install` creates, so CloudHop cannot "
             "check its version, CSI support and plugin.")
        return
    versions[side] = velero["version"]
    problems = velero_problems(velero)
    if problems:
        _add(checks, "velero", side, MISSING, f"Velero {velero['version']} is installed, but {'; '.join(problems)}.")
    else:
        plugin = velero["plugins"].get(GCP_PLUGIN)
        _add(checks, "velero", side, OK,
             f"Velero {velero['version']} is installed, with CSI support and GCP plugin {plugin}.")


def _check_snapshot_class(kube: KubeClient, checks: list) -> None:
    try:
        classes = kube.list("/apis/snapshot.storage.k8s.io/v1/volumesnapshotclasses")
    except KubeAPIError as exc:
        if exc.status_code == 404:
            _add(checks, "snapshot_class", "source", MISSING, "The cluster has no CSI snapshot API.")
        else:
            _add(checks, "snapshot_class", "source", UNKNOWN, f"CloudHop cannot list snapshot classes: {exc}")
        return
    labelled = [
        item["metadata"]["name"] for item in classes
        if (item["metadata"].get("labels") or {}).get(SNAPSHOT_CLASS_LABEL) == "true"
    ]
    if labelled:
        _add(checks, "snapshot_class", "source", OK, f"Velero snapshots volumes with class {labelled[0]}.")
    else:
        _add(checks, "snapshot_class", "source", MISSING,
             f"No VolumeSnapshotClass labelled {SNAPSHOT_CLASS_LABEL}=true; needed to copy volumes.")


def _check_bucket(account, locations: dict, name: str, checks: list) -> str:
    """The bucket exists in the target's account, CloudHop and Velero may use it, and both storage
    locations do.

    Checks the bucket Velero already uses if it is installed, else the name given; returns the one checked.
    """
    source, target = locations.get("source"), locations.get("target")
    if source and target and (source.get("bucket"), source.get("prefix")) != (target.get("bucket"), target.get("prefix")):
        _add(checks, "bucket", "", MISSING,
             f"The source uses {_gs_url(source)} and the target {_gs_url(target)}; both must use the same "
             f"bucket and prefix.")
        return target.get("bucket") or ""
    name = (target or source or {}).get("bucket") or name
    if not name:
        _add(checks, "bucket", "", UNKNOWN, "Enter the bucket's name.")
        return ""

    adapter = BucketAdapter.for_account(account)
    try:
        bucket = adapter.get_bucket(name)
    except CloudAPIError as exc:
        reason = str(exc) if exc.status_code == 403 else f"CloudHop cannot read gs://{name}: {exc}"
        _add(checks, "bucket", "", UNKNOWN, reason)
        # Unreadable is not missing: the bucket's objects may still be open to Velero
        if exc.status_code == 403:
            _check_backup_access(adapter, name, checks)
        return name
    if bucket is None:
        _add(checks, "bucket", "", MISSING, f"There is no bucket gs://{name} yet.")
        _add(checks, "bucket_access", "", UNKNOWN, "Create the bucket first.")
        return name
    if not bucket["in_account"]:
        _add(checks, "bucket", "", WARNING,
             f"gs://{name} belongs to {bucket['owner']}, not to the destination's project {account.project_id}. "
             f"Put it in the destination account, which keeps its billing after the source's stops.")
    else:
        used = f" Both clusters use {_gs_url(source)}." if source and target else ""
        _add(checks, "bucket", "", OK,
             f"gs://{name} exists in {account.project_id} ({bucket['location']}), and CloudHop can read it.{used}")
    _check_backup_access(adapter, name, checks)
    return name


def _check_backup_access(adapter: BucketAdapter, name: str, checks: list) -> None:
    """Velero reuses CloudHop's service account in the target account: may it write and read backups?"""
    who = adapter.identity()
    try:
        missing = adapter.missing_backup_access(name)
    except CloudAPIError as exc:
        _add(checks, "bucket_access", "", UNKNOWN, f"CloudHop cannot tell what {who} may do in gs://{name}: {exc}")
        return
    if missing:
        _add(checks, "bucket_access", "", MISSING,
             f"Velero, as {who}, may not {', '.join(missing)} in gs://{name}. Grant it {adapter.backup_role} on the "
             f"bucket.")
    else:
        _add(checks, "bucket_access", "", OK, f"Velero, as {who}, can write and read backups in gs://{name}.")


def _check_access(kube: KubeClient, side: str, needs: list[tuple], checks: list) -> None:
    """Ask the cluster whether CloudHop may do each thing a move does there."""
    denied = []
    for verb, group, resource, namespace in needs:
        resource, _, subresource = resource.partition("/")
        attributes = {"verb": verb, "group": group, "resource": resource, "subresource": subresource}
        if namespace:
            attributes["namespace"] = namespace
        try:
            review = kube.post(ACCESS_REVIEW, {
                "apiVersion": "authorization.k8s.io/v1",
                "kind": "SelfSubjectAccessReview",
                "spec": {"resourceAttributes": attributes},
            })
        except KubeAPIError as exc:
            _add(checks, "access", side, UNKNOWN, f"CloudHop cannot ask the cluster what it may do: {exc}")
            return
        if not (review.get("status") or {}).get("allowed"):
            name = f"{resource}.{group}" if group else resource
            denied.append(f"{verb} {name}{'/' + subresource if subresource else ''}"
                          f"{' in ' + namespace if namespace else ''}")
    if denied:
        _add(checks, "access", side, MISSING, f"CloudHop may not {', '.join(denied)}.")
    else:
        _add(checks, "access", side, OK, "CloudHop has the access a move needs.")


def _get(kube: KubeClient, path: str) -> dict | None:
    try:
        return kube.get(path)
    except KubeAPIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _seconds(duration: str | None) -> float:
    """Seconds in a Kubernetes duration like "1m0s" or "10s"; no duration is Velero's default, a minute."""
    if not duration:
        return 60
    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(amount) * units[unit] for amount, unit in DURATION_PART.findall(duration))


def _gs_url(storage: dict) -> str:
    bucket, prefix = storage.get("bucket") or "?", storage.get("prefix")
    return f"gs://{bucket}/{prefix}" if prefix else f"gs://{bucket}"


def _add(checks: list, key: str, side: str, status: str, detail: str) -> None:
    checks.append({"key": key, "cluster": side, "status": status, "detail": detail})
