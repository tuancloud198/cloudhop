from abc import abstractmethod
from functools import cached_property

from accounts.models import Account
from clusters.models import Clusters
from common.cloud import ProviderAdapter, load_credential_file
from common.kube import KubeClient


class KubeAccessAdapter(ProviderAdapter):
    """Opens a connection to the Kubernetes API of an Account's cluster, and tells
    the objects the cluster made for itself apart from the ones users created.

    The rules below hold on every Kubernetes cluster. A provider adapter extends
    them with what its managed service installs (e.g. GKE's gke-* namespaces).
    """

    # (group, resource) never listed
    skipped_resources: frozenset[tuple[str, str]] = frozenset({
        # Written by controllers and changing every few seconds
        ("", "events"),
        ("events.k8s.io", "events"),
        ("", "endpoints"),
        ("discovery.k8s.io", "endpointslices"),
        ("coordination.k8s.io", "leases"),
        # Describe the cluster's own machinery, never created by users
        ("", "nodes"),
        ("", "componentstatuses"),
        ("storage.k8s.io", "csinodes"),
        ("storage.k8s.io", "volumeattachments"),
        ("certificates.k8s.io", "certificatesigningrequests"),
        # Allocated by the API server for each Service ClusterIP and from cluster flags
        ("networking.k8s.io", "ipaddresses"),
        ("networking.k8s.io", "servicecidrs"),
        ("flowcontrol.apiserver.k8s.io", "flowschemas"),
        ("flowcontrol.apiserver.k8s.io", "prioritylevelconfigurations"),
        ("apiregistration.k8s.io", "apiservices"),
        ("admissionregistration.k8s.io", "validatingadmissionpolicies"),
        ("admissionregistration.k8s.io", "validatingadmissionpolicybindings"),
    })
    # Namespaces run by the cluster; their objects (and the Namespace itself) are system
    system_namespace_prefixes: tuple[str, ...] = ("kube-",)
    # Label keys put on objects the cluster creates and reconciles
    system_labels: frozenset[str] = frozenset({"kubernetes.io/bootstrapping"})
    # Cluster-scoped names of built-in RBAC and priority classes
    system_name_prefixes: tuple[str, ...] = ("system:", "system-")
    # Domains the cluster owns; cluster-scoped objects named under them
    # (CRDs, webhooks) are installed by the cluster, not by users
    system_name_suffixes: tuple[str, ...] = (".k8s.io",)
    # (group, kind, name) created automatically in every namespace
    default_objects: frozenset[tuple[str, str, str]] = frozenset({
        ("", "ServiceAccount", "default"),
        ("", "ConfigMap", "kube-root-ca.crt"),
    })

    def __init__(self, account: Account):
        self.account = account

    @classmethod
    def for_account(cls, account: Account) -> "KubeAccessAdapter":
        return cls.for_provider(account.provider)(account)

    @cached_property
    def credential(self) -> dict:
        return load_credential_file(self.account.credential_ref)

    @abstractmethod
    def connect(self, cluster: Clusters) -> KubeClient:
        """Return a KubeClient (not yet entered) for the cluster's API server.

        Raises CloudAPIError if the endpoint or a token cannot be obtained.
        """

    def is_user_created(self, group: str, kind: str, item: dict) -> bool:
        """False for objects made by the cluster, its provider or a controller rather than by a user."""
        metadata = item.get("metadata", {})
        name = metadata.get("name", "")
        namespace = metadata.get("namespace", "")

        if self.is_system_namespace(namespace):
            return False
        # "default" always exists; user objects inside it are kept
        if not group and kind == "Namespace" and (name == "default" or self.is_system_namespace(name)):
            return False
        # Pods of a ReplicaSet, ReplicaSets of a Deployment, Jobs of a CronJob, ...
        if metadata.get("ownerReferences"):
            return False
        if self.system_labels & set(metadata.get("labels") or {}):
            return False
        if not namespace and (
            name.startswith(self.system_name_prefixes) or name.endswith(self.system_name_suffixes)
        ):
            return False
        if (group, kind, name) in self.default_objects:
            return False
        if not group and kind == "Service" and namespace == "default" and name == "kubernetes":
            return False
        # Made by a provisioner for a PersistentVolumeClaim, which is what the user created
        if not group and kind == "PersistentVolume" and "pv.kubernetes.io/provisioned-by" in (
            metadata.get("annotations") or {}
        ):
            return False
        return True

    def is_system_namespace(self, namespace: str) -> bool:
        return namespace.startswith(self.system_namespace_prefixes)
