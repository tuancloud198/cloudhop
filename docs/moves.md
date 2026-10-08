# Moves

A **move** copies namespaces from a source cluster to a target cluster in another account, with every object and the data of their volumes, so the workloads keep running when the source account's credits run out.

[Velero](https://velero.io) does the copy, through a Cloud Storage bucket in the target account. CloudHop starts each step, scales the workloads down and up for a cutover, and checks the result. It runs on Google Cloud (GKE to GKE); other providers come later.

Setting up Velero is described in the Guide, under *Moving namespaces*.

## Why Velero, and why a bucket

| Option | Verdict |
|---|---|
| **Velero with the CSI snapshot data mover** | Chosen. It backs up every Kubernetes object of the namespaces and copies the volumes' files into a bucket; its restore recreates both in another cluster. It already handles what a new cluster needs: Services get new cluster IPs, PVCs bind to new volumes, objects are restored in a safe order, and custom resources move too. |
| pv-migrate (rsync between clusters) | Copies volumes only, needs both clusters reachable from each other at once, and keeps no copy to fall back on. |
| GCP disk snapshots shared across projects | GCP only, and CloudHop would have to recreate disks, PVs and every other object itself. |
| Applying CloudHop's stored manifests | Covers built-in kinds only (no custom resources), and every manifest would need rewriting for the new cluster. |

Velero cannot copy from one cluster straight into another: the source writes the backup to object storage and the target reads it. The bucket lives in the **target** account. The source account is the one about to lose its credits, and once its billing stops, anything stored in its project may be unreachable.

Velero's plain snapshot mode would leave the volume data in disk snapshots inside the source project, unusable from another account. The data mover (`snapshotMoveData: true`) copies it into the bucket instead.

## Setup (once, by the user)

In the target project: a bucket, and a service account `velero` with `roles/storage.objectAdmin` on it, whose key Velero uses in both clusters.

In each cluster:

- Velero 1.14 or later with its node agent and the GCP plugin, storage location `default` on the bucket. The target's is read-only (`accessMode: ReadOnly`).
- In the source: a `VolumeSnapshotClass` for `pd.csi.storage.gke.io`, labelled `velero.io/csi-volumesnapshot-class: "true"`.
- RBAC for CloudHop's service account (`cloudhop-mover`): create and read Velero backups and restores, read storage locations and snapshot classes, scale Deployments and StatefulSets, and write ConfigMaps in the `velero` namespace.

## A move, step by step

```
pending → checking → [cutover: scaling_down] → backing_up → restoring → [cutover: scaling_up] → verifying → done
                                         any step → failed (retry from that step) | cancelled
```

1. **Checking:** Velero runs in both clusters, both storage locations are `Available` and point at the same bucket and prefix, every namespace exists in the source and not in the target, every storage class the source's volumes use exists in the target (after the mapping), and the source has a snapshot class for Velero.
2. **Scaling down** (cutover only): CloudHop records the replicas of every Deployment and StatefulSet, scales them to 0, and waits for their pods to stop, so nothing writes to the volumes during the backup.
3. **Backing up:** a Velero `Backup` of the namespaces in the source, with `snapshotMoveData: true` and a 7-day TTL, so a failed restore can be retried.
4. **Restoring:** once the target's Velero has found the backup in the bucket (it lists it every minute by default), a Velero `Restore` in the target. Storage classes the target names differently are renamed through Velero's `change-storage-class-config` ConfigMap.
5. **Scaling up** (cutover only): the restored workloads, which were backed up at 0, get the replicas recorded in step 2.
6. **Verifying:** every Deployment and StatefulSet in the target has its replicas ready, and every PVC is bound.

The source is never deleted. After a cutover it stays scaled to 0, so it can be scaled back up if the move is abandoned.

Modes:

- **Cutover** (default): the steps above. Consistent data; the app is down from step 2 until step 6.
- **Copy**: no scaling; the backup is taken while the source runs, so both run afterwards. Volumes of running databases may be copied mid-write. For trying a move.

Each step that waits (pods stopping, Velero working, workloads starting) has a time limit; past it the move fails with what it was waiting for. A failed move can be retried from the step it failed at; a retried backup or restore gets a new Velero name (`cloudhop-move-<id>-<attempt>`). Cancelling stops a move after its current step and undoes nothing.

## Code

- `moves/models/`: `Move` (clusters, namespaces, mode, storage class mapping, status, Velero object names, recorded replicas, timestamps) and `MoveEvent` (the move's log).
- `moves/services/`: `create_move`, `advance` (runs the current step once), `cancel_move`, `retry_move`. Each step checks what is already done first, so running it again is safe; `advance` locks the move's row so two workers never run the same move at once.
- `moves/tasks.py`: `advance_move` runs a step and queues itself again, every 15 seconds while a step waits. `resume_moves`, every 5 minutes, queues running moves not checked for 5 minutes, e.g. after the worker restarted.
- `moves/views/`: `/api/v1/moves/` (list, start), `/api/v1/moves/<id>/` (with its log), `.../cancel/`, `.../retry/`.
- `common/kube/`: `KubeClient` can now create (`post`) and merge-patch (`patch`) objects.
- Frontend: **Moves** in the sidebar (`MovesView`, `MoveView`), and **Move namespaces** on a cluster.

The `kubernetes` app's resource sync lists built-in kinds only, so Velero's own objects are never stored.

## Limits and open questions

- **One move at a time per cluster.**
- **Namespaces only.** Cluster-scoped objects the namespaces depend on (ClusterRoles, PriorityClasses, StorageClasses) are not moved; Velero restores those it finds related, and the check catches missing storage classes.
- **New external IPs.** LoadBalancer Services and Ingresses get new addresses; DNS is up to the user.
- **Images** in the source project's Artifact Registry stop being pullable once its billing stops.
- **Egress** out of the source region is billed to the source, so a move uses some of the credits it is trying to save.
- Later: starting the backup automatically when `spend_status()` crosses a threshold (the cutover still waiting for the user), CloudHop installing Velero itself, other providers.
