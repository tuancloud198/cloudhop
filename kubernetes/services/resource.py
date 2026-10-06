import logging

from django.db import transaction
from django.db.models import Count, Max
from django.utils.dateparse import parse_datetime

from clusters.models import Clusters
from common.kube import KubeAPIError, KubeClient

from ..adapters import KubeAccessAdapter
from ..models import KubeResource

__all__ = ["ClusterNotUsable", "list_native_resource_types", "resource_summary", "sync_resources"]

logger = logging.getLogger(__name__)

# The API server labels the APIService of each built-in group version "onstart";
# CRD groups get "true" and aggregated APIs (e.g. metrics.k8s.io) get no label
NATIVE_LABEL = "kube-aggregator.kubernetes.io/automanaged"

# Set by the API server, meaningless on another cluster
SERVER_METADATA = {"uid", "resourceVersion", "generation", "creationTimestamp", "managedFields", "selfLink"}
LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"

# Listing a kind is skipped (not fatal) on these: no RBAC access, or the kind went away
SKIPPABLE_STATUS = {403, 404, 405}


class ClusterNotUsable(Exception):
    """The cluster is inactive, or its account is inactive or not validated."""


def sync_resources(cluster_id: int) -> dict:
    """Fetch the user-created objects of every native kind in the cluster and store them.

    Objects the cluster made for itself are not stored; the account's
    KubeAccessAdapter decides which those are.

    The cluster's stored objects are replaced by what the cluster returns now,
    in one transaction, so a failed fetch leaves them untouched. Kinds the
    credential cannot list are reported as skipped and end up with no rows.

    Returns {"synced": {kind: count}, "skipped": [{group, kind, reason}]}.
    Raises Clusters.DoesNotExist, ClusterNotUsable, or common.cloud.CloudAPIError.
    """
    cluster = Clusters.objects.select_related("account_id").get(pk=cluster_id)
    account = cluster.account_id
    if not cluster.is_active:
        raise ClusterNotUsable(f"cluster {cluster_id} is inactive")
    if not account.is_active or not account.is_valid:
        raise ClusterNotUsable(f"account {account.pk} is inactive or not validated")

    adapter = KubeAccessAdapter.for_account(account)
    rows, skipped = [], []
    with adapter.connect(cluster) as kube:
        for resource_type in list_native_resource_types(kube, adapter.skipped_resources):
            try:
                items = kube.list(resource_type["path"])
            except KubeAPIError as exc:
                if exc.status_code not in SKIPPABLE_STATUS:
                    raise
                skipped.append({
                    "group": resource_type["group"],
                    "kind": resource_type["kind"],
                    "reason": str(exc),
                })
                continue
            rows.extend(
                _to_row(cluster, resource_type, item)
                for item in items
                if adapter.is_user_created(resource_type["group"], resource_type["kind"], item)
            )

    with transaction.atomic():
        KubeResource.objects.filter(cluster=cluster).delete()
        KubeResource.objects.bulk_create(rows, batch_size=500)

    counts = {}
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return {"synced": counts, "skipped": skipped}


def resource_summary(cluster_id: int) -> dict:
    """Describe the cluster's stored objects, for filtering them.

    Returns {"kinds": [{group, kind, count}], "namespaces": [name], "synced_at": datetime or None}.
    """
    resources = KubeResource.objects.filter(cluster_id=cluster_id)
    kinds = resources.values("group", "kind").annotate(count=Count("id")).order_by("kind", "group")
    namespaces = (
        resources.exclude(namespace="").values_list("namespace", flat=True).distinct().order_by("namespace")
    )
    return {
        "kinds": list(kinds),
        "namespaces": list(namespaces),
        # Every sync replaces all rows, so their creation time is the last sync time
        "synced_at": resources.aggregate(synced_at=Max("created_at"))["synced_at"],
    }


def list_native_resource_types(kube: KubeClient, skipped: frozenset[tuple[str, str]] = frozenset()) -> list[dict]:
    """Return the listable native kinds served by the cluster, at each group's preferred version.

    Kinds whose (group, resource) is in skipped are left out.

    Each dict has group, version, kind, namespaced and path (the list URL).
    """
    apiservices = kube.get("/apis/apiregistration.k8s.io/v1/apiservices")["items"]
    native_versions = {
        (service["spec"].get("group", ""), service["spec"]["version"])
        for service in apiservices
        if service["metadata"].get("labels", {}).get(NATIVE_LABEL) == "onstart"
    }

    group_versions = [("", "v1")] + [
        (group["name"], group["preferredVersion"]["version"])
        for group in kube.get("/apis")["groups"]
    ]

    resource_types = []
    for group, version in group_versions:
        if (group, version) not in native_versions:
            continue
        base = f"/apis/{group}/{version}" if group else f"/api/{version}"
        for resource in kube.get(base)["resources"]:
            if "/" in resource["name"]:  # subresource, e.g. pods/log
                continue
            if "list" not in resource.get("verbs", []):
                continue
            if (group, resource["name"]) in skipped:
                continue
            resource_types.append({
                "group": group,
                "version": version,
                "kind": resource["kind"],
                "namespaced": resource["namespaced"],
                "path": f"{base}/{resource['name']}",
            })
    return resource_types


def _to_row(cluster: Clusters, resource_type: dict, item: dict) -> KubeResource:
    metadata = item.get("metadata", {})
    return KubeResource(
        cluster=cluster,
        group=resource_type["group"],
        version=resource_type["version"],
        kind=resource_type["kind"],
        namespace=metadata.get("namespace", ""),
        name=metadata["name"],
        uid=metadata.get("uid", ""),
        manifest=_clean_manifest(resource_type, item),
        status=item.get("status") or {},
        kube_created_at=parse_datetime(metadata.get("creationTimestamp") or ""),
    )


def _clean_manifest(resource_type: dict, item: dict) -> dict:
    """Strip what the API server sets, keeping what is needed to re-create the object."""
    group, version = resource_type["group"], resource_type["version"]
    # Items in a list response carry no apiVersion or kind
    manifest = {
        "apiVersion": f"{group}/{version}" if group else version,
        "kind": resource_type["kind"],
        **{key: value for key, value in item.items() if key not in ("apiVersion", "kind", "status")},
    }

    metadata = {key: value for key, value in item.get("metadata", {}).items() if key not in SERVER_METADATA}
    annotations = {key: value for key, value in metadata.get("annotations", {}).items() if key != LAST_APPLIED}
    if annotations:
        metadata["annotations"] = annotations
    else:
        metadata.pop("annotations", None)
    manifest["metadata"] = metadata

    if not group and resource_type["kind"] == "Secret":
        # Never store secret values in the database; keep the key names only
        manifest["data"] = dict.fromkeys(item.get("data") or {}, "")
        manifest.pop("stringData", None)
    return manifest
