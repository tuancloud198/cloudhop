from django.db import models


# One object of a native (built-in) Kubernetes kind inside a cluster, e.g. a Deployment
class KubeResource(models.Model):
    cluster = models.ForeignKey(
        "clusters.Clusters",
        on_delete=models.CASCADE,
        related_name="resources",
    )

    # API group, "" for the core group (Pod, Service, ConfigMap, ...)
    group = models.CharField(max_length=253, blank=True, default="")
    # Version the object was read with (the group's preferred version)
    version = models.CharField(max_length=50)
    kind = models.CharField(max_length=100)
    # "" for cluster-scoped kinds (Namespace, Node, ClusterRole, ...)
    namespace = models.CharField(max_length=253, blank=True, default="")
    name = models.CharField(max_length=253)
    # Changes when the object is deleted and recreated under the same name
    uid = models.CharField(max_length=64)

    # Object without status and server-managed metadata, ready to re-apply to another cluster
    manifest = models.JSONField(default=dict)
    status = models.JSONField(default=dict, blank=True)

    kube_created_at = models.DateTimeField(null=True, blank=True)
    # Every sync replaces the cluster's rows, so this is when the object was last synced
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cluster", "group", "kind", "namespace", "name"],
                name="unique_cluster_resource",
            )
        ]
        indexes = [
            models.Index(fields=["cluster", "kind"]),
            models.Index(fields=["cluster", "namespace"]),
        ]
