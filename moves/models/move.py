from django.db import models


class Move(models.Model):
    """Copies the objects and volume data of namespaces from one cluster to another with Velero.

    A move goes through its steps in order (see Status); a Celery task runs the current
    step until it is done, then the next one.
    """

    class Mode(models.TextChoices):
        # Stops the source workloads before the last backup: consistent data, some downtime
        CUTOVER = "cutover", "Cutover"
        # Backs up while the source runs: crash-consistent data, no downtime
        COPY = "copy", "Copy"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CHECKING = "checking", "Checking"
        SCALING_DOWN = "scaling_down", "Scaling down the source"
        BACKING_UP = "backing_up", "Backing up"
        RESTORING = "restoring", "Restoring"
        SCALING_UP = "scaling_up", "Scaling up the target"
        VERIFYING = "verifying", "Verifying"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    source_cluster = models.ForeignKey("clusters.Clusters", on_delete=models.CASCADE, related_name="moves_out")
    target_cluster = models.ForeignKey("clusters.Clusters", on_delete=models.CASCADE, related_name="moves_in")
    namespaces = models.JSONField(default=list)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.CUTOVER)
    # Source storage class name -> target storage class name, for classes the target names differently
    storage_class_mapping = models.JSONField(default=dict, blank=True)
    # Velero BackupStorageLocation, set up in both clusters on the same bucket
    storage_location = models.CharField(max_length=253, default="default")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # What the current step is waiting for, e.g. "Backup InProgress: 12 of 40 items"
    waiting_on = models.TextField(blank=True, default="")
    # The step a failed move stopped at, and why; retrying runs that step again
    failed_step = models.CharField(max_length=20, choices=Status.choices, blank=True, default="")
    error = models.TextField(blank=True, default="")
    # Raised on each retry of a backup or restore, so it gets a new Velero object name
    attempt = models.PositiveIntegerField(default=1)
    backup_name = models.CharField(max_length=253, blank=True, default="")
    restore_name = models.CharField(max_length=253, blank=True, default="")
    # "namespace/Kind/name" -> replicas before the cutover scaled the source down
    replicas = models.JSONField(default=dict, blank=True)

    step_started_at = models.DateTimeField(null=True, blank=True)
    # Last time a step ran, even if it only waited; a move not checked for a while lost its task
    checked_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_cluster=models.F("target_cluster")), name="move_between_two_clusters"
            ),
        ]

    def __str__(self):
        return f"Move {self.pk}: {self.source_cluster} -> {self.target_cluster} ({self.status})"
