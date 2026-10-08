import base64

from accounts.models import Account
from clusters.models import Clusters
from common.cloud.gcp import GCPClient
from common.kube import KubeClient
from kubernetes.adapters.base import KubeAccessAdapter

# {{project_id}} is left for GCPClient to fill
CLUSTER_URL = (
    "https://container.googleapis.com/v1/projects/{{project_id}}"
    "/locations/{location}/clusters/{name}"
)


class GCPKubeAccessAdapter(KubeAccessAdapter):
    provider = Account.Provider.GCP

    # GKE runs its add-ons in gke-* namespaces and Managed Prometheus in gmp-*
    system_namespace_prefixes = KubeAccessAdapter.system_namespace_prefixes + ("gke-", "gmp-")
    # Set by the addon manager that installs and reconciles GKE add-ons
    system_labels = KubeAccessAdapter.system_labels | {
        "addonmanager.kubernetes.io/mode",
        "kubernetes.io/cluster-service",
    }
    system_name_prefixes = KubeAccessAdapter.system_name_prefixes + ("gce:", "gke-")
    # e.g. CRD backendconfigs.cloud.google.com, webhook pod-ready.config.common-webhooks.networking.gke.io
    system_name_suffixes = KubeAccessAdapter.system_name_suffixes + (
        ".gke.io", ".google.com", ".googleapis.com",
    )

    def connect(self, cluster: Clusters) -> KubeClient:
        """Connect to a GKE cluster's public endpoint as the service account.

        GKE accepts the service account's OAuth token; what it can read is set
        by its IAM roles (e.g. roles/container.viewer) and the cluster's RBAC.
        """
        client = GCPClient(self.credential, self.account.project_id)
        # Read fresh rather than from cluster.spec: the CA is not stored and the endpoint can change
        data = client.get(CLUSTER_URL.format(location=cluster.location, name=cluster.name))
        return KubeClient(
            endpoint=f"https://{data['endpoint']}",
            ca_cert=base64.b64decode(data["masterAuth"]["clusterCaCertificate"]).decode(),
            token=client.access_token(),
        )
