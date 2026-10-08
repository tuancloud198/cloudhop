import logging

from accounts.models import Account
from clusters.adapters.base import ClusterAdapter
from common.cloud import CloudAPIError
from common.cloud.gcp import GCPClient

logger = logging.getLogger(__name__)

CLUSTERS_URL = "https://container.googleapis.com/v1/projects/{project_id}/locations/-/clusters"
# {{project_id}} is left for GCPClient to fill
MACHINE_TYPE_URL = (
    "https://compute.googleapis.com/compute/v1/projects/{{project_id}}"
    "/zones/{zone}/machineTypes/{machine_type}"
)


class GCPClusterAdapter(ClusterAdapter):
    provider = Account.Provider.GCP

    def list_clusters(self) -> list[dict]:
        """List GKE clusters in all locations of the project."""
        self._client = GCPClient(self.credential, self.account.project_id)
        self._machine_types = {}
        data = self._client.get(CLUSTERS_URL)
        if data.get("missingZones"):
            # Some locations could not be queried, so the list is incomplete
            raise CloudAPIError(
                f"GKE did not answer for locations: {', '.join(data['missingZones'])}"
            )
        return [self._normalize_cluster(cluster) for cluster in data.get("clusters", [])]

    def _normalize_cluster(self, cluster: dict) -> dict:
        return {
            "external_id": cluster["id"],
            "name": cluster["name"],
            "location": cluster["location"],
            "status": cluster.get("status", ""),
            "kubernetes_version": cluster.get("currentMasterVersion"),
            "spec": {
                "node_count": cluster.get("currentNodeCount", 0),
                "node_pools": [
                    self._normalize_node_pool(pool, cluster)
                    for pool in cluster.get("nodePools", [])
                ],
                "network": cluster.get("network"),
                "subnetwork": cluster.get("subnetwork"),
                "endpoint": cluster.get("endpoint"),
                "autopilot": cluster.get("autopilot", {}).get("enabled", False),
                "release_channel": cluster.get("releaseChannel", {}).get("channel"),
            },
        }

    def _normalize_node_pool(self, pool: dict, cluster: dict) -> dict:
        config = pool.get("config", {})
        machine_type = config.get("machineType")
        zones = pool.get("locations") or cluster.get("locations") or []
        machine = self._machine_type(zones[0], machine_type) if zones and machine_type else {}
        return {
            "name": pool["name"],
            "machine_type": machine_type,
            "node_count": pool.get("initialNodeCount"),
            "autoscaling": pool.get("autoscaling", {}),
            "version": pool.get("version"),
            # Per node
            "cpu": machine.get("guestCpus"),
            "memory_mb": machine.get("memoryMb"),
            "disk_size_gb": config.get("diskSizeGb"),
            "disk_type": config.get("diskType"),
        }

    def _machine_type(self, zone: str, machine_type: str) -> dict:
        """CPU and memory of a machine type, from Compute Engine. Empty if the lookup fails."""
        key = (zone, machine_type)
        if key not in self._machine_types:
            url = MACHINE_TYPE_URL.format(zone=zone, machine_type=machine_type)
            try:
                self._machine_types[key] = self._client.get(url)
            except CloudAPIError as exc:
                # Sizing is informational, so it should not fail the whole sync
                logger.warning("Cannot look up machine type %s in %s: %s", machine_type, zone, exc)
                self._machine_types[key] = {}
        return self._machine_types[key]
