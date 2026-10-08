# Moves

A **move** copies namespaces from a source cluster to a target cluster in another account, so the workloads keep running when the source account's credits run out. It runs on Google Cloud (GKE to GKE); other providers come later.

A move has a **method**:

- **Manifests only** (`manifests`): CloudHop reads the namespaces' objects from the source and server-side-applies them to the target. Nothing to install, but no volume data and no custom resources.
- **Velero** (`velero`): [Velero](https://velero.io) copies every object and the data of their volumes, through a Cloud Storage bucket in the target account.

and a **mode**:

- **Cutover** (`cutover`): the source's Deployments and StatefulSets end up scaled to 0.
- **Keep running** (`copy`): the source is left alone, so both run afterwards.

The source is never deleted. Setting up either method is described in the Guide, under *Moving namespaces*.

## Manifests only

```
pending → checking → copying → verifying → [cutover: scaling_down] → done
```

1. **Checking:** every namespace exists in the source and not in the target. CloudHop reads every object of a built-in kind in them, the same ones the resource sync stores, and fails if it cannot read a kind (it would be left behind), if a namespace has PersistentVolumeClaims (no volume data is copied), or if it has custom resources (only built-in kinds are). Custom kinds CloudHop may not list are a warning.
2. **Copying:** the objects are read again and applied to the target with server-side apply (field manager `cloudhop`), in order: the Namespace, ServiceAccounts, Secrets, ConfigMaps, quotas, RBAC, NetworkPolicies and Services, then every other kind, then the workloads, HPAs, PDBs and Ingresses. Before applying, CloudHop drops what the source cluster set: server metadata, finalizers, Service cluster IPs and node ports, a Pod's node, a Job's generated selector, and the token of service-account-token Secrets. Jobs and Pods that already finished are skipped, so they do not run again. Every object is tried; any that failed are listed and fail the step. Applying is idempotent, so a retry applies everything again.
3. **Verifying:** as with Velero, every Deployment and StatefulSet in the target has its replicas ready.
4. **Scaling down** (cutover only): only now, with the target running, the source's workloads are scaled to 0 and their replicas recorded. No downtime, but both run for a moment.

In the target, CloudHop needs to create namespaces and write inside them (the built-in `admin` role); for a cutover, to scale workloads in the source. The Guide has the RBAC.

## With Velero

### Why Velero, and why a bucket

| Option | Verdict |
|---|---|
| **Velero with the CSI snapshot data mover** | Chosen. It backs up every Kubernetes object of the namespaces and copies the volumes' files into a bucket; its restore recreates both in another cluster. It already handles what a new cluster needs: Services get new cluster IPs, PVCs bind to new volumes, objects are restored in a safe order, and custom resources move too. |
| pv-migrate (rsync between clusters) | Copies volumes only, needs both clusters reachable from each other at once, and keeps no copy to fall back on. |
| GCP disk snapshots shared across projects | GCP only, and CloudHop would have to recreate disks, PVs and every other object itself. |
| Applying the objects read from the source | Covers built-in kinds only (no custom resources) and no volumes. Kept as the *manifests only* method for namespaces without either. |

Velero cannot copy from one cluster straight into another: the source writes the backup to object storage and the target reads it. The bucket lives in the **target** account. The source account is the one about to lose its credits, and once its billing stops, anything stored in its project may be unreachable.

Velero's plain snapshot mode would leave the volume data in disk snapshots inside the source project, unusable from another account. The data mover (`snapshotMoveData: true`) copies it into the bucket instead.

### Setup (once, by the user)

In the target project: a bucket, and a service account `velero` with `roles/storage.objectAdmin` on it, whose key Velero uses in both clusters.

In each cluster:

- Velero 1.14 or later with its node agent and the GCP plugin, storage location `default` on the bucket. The target's is read-only (`accessMode: ReadOnly`).
- In the source: a `VolumeSnapshotClass` for `pd.csi.storage.gke.io`, labelled `velero.io/csi-volumesnapshot-class: "true"`.
- RBAC for CloudHop's service account (`cloudhop-mover`): create and read Velero backups and restores, read storage locations and snapshot classes, scale Deployments and StatefulSets, and write ConfigMaps in the `velero` namespace.

### A move, step by step

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
- **Keep running** (`copy`): no scaling; the backup is taken while the source runs, so both run afterwards. Volumes of running databases may be copied mid-write. For trying a move.

Each step that waits (pods stopping, Velero working, workloads starting) has a time limit; past it the move fails with what it was waiting for. A failed move can be retried from the step it failed at; a retried backup or restore gets a new Velero name (`cloudhop-move-<id>-<attempt>`). Cancelling stops a move after its current step and undoes nothing.

## Code

- `moves/models/`: `Move` (clusters, namespaces, method, mode, storage class mapping, status, Velero object names, recorded replicas, timestamps) and `MoveEvent` (the move's log).
- `moves/services/`: `create_move`, `advance` (runs the current step once), `cancel_move`, `retry_move`. Each step checks what is already done first, so running it again is safe; `advance` locks the move's row so two workers never run the same move at once.
- `moves/tasks.py`: `advance_move` runs a step and queues itself again, every 15 seconds while a step waits. `resume_moves`, every 5 minutes, queues running moves not checked for 5 minutes, e.g. after the worker restarted.
- `moves/views/`: `/api/v1/moves/` (list, start), `/api/v1/moves/<id>/` (with its log), `.../cancel/`, `.../retry/`.
- `common/kube/`: `KubeClient` can create (`post`), merge-patch (`patch`) and server-side apply (`apply`) objects.
- `kubernetes/services/resource.py`: `list_native_resource_types`, `list_custom_resource_types` and `clean_manifest`, shared by the resource sync and manifests-only moves.
- Frontend: **Moves** in the sidebar (`MovesView`, `MoveView`), and **Move namespaces** on a cluster.

The `kubernetes` app's resource sync lists built-in kinds only, so Velero's own objects are never stored.

## Limits and open questions

- **One move at a time per cluster.**
- **Namespaces only.** Cluster-scoped objects the namespaces depend on (ClusterRoles, PriorityClasses, StorageClasses) are not moved; Velero restores those it finds related, and the check catches missing storage classes. Manifests-only moves copy none of them.
- **Manifests only:** Services with fixed node ports get new ones, and RoleBindings can only grant what CloudHop itself holds in the target.
- **New external IPs.** LoadBalancer Services and Ingresses get new addresses; DNS is up to the user.
- **Images** in the source project's Artifact Registry stop being pullable once its billing stops.
- **Egress** out of the source region is billed to the source, so a move uses some of the credits it is trying to save.
- Later: starting the backup automatically when `spend_status()` crosses a threshold (the cutover still waiting for the user), CloudHop installing Velero itself, other providers.
