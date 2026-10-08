# Moves: design

Status: proposal. Nothing here is built yet.

A **move** copies the workloads of one or more namespaces from a source cluster to a target cluster in another account, with their data, so they keep running when the source account's credits run out.

CloudHop already stores every user-created object of a cluster as a clean manifest. That covers the compute side (Deployments, Services, ConfigMaps, Secrets, ...): those can be applied to another cluster. What it cannot do is move the data in PersistentVolumeClaims. This design adds that with [Velero](https://velero.io), and the steps around it.

## Scope

First version:

- GKE to GKE, across accounts (GCP projects with separate billing accounts).
- The user starts a move and confirms the cutover.
- Velero is installed in both clusters by the user, following the Guide; CloudHop checks it is there.

Later:

- Moves to and from other providers (AWS, Azure), once they are supported. The data path below already works across providers.
- Starting the backup automatically when a budget runs low (see [Triggering](#triggering)).
- CloudHop installing Velero itself.

## Moving the data

### Options considered

| Option | Why not, or not first |
|---|---|
| **Velero, CSI snapshot data mover** | Chosen. Copies volume data through an object bucket, so the source and target only need to reach the bucket, not each other, and the target can use any storage class or provider. Driven entirely through Kubernetes objects, which CloudHop already talks to. |
| pv-migrate (rsync between clusters) | Needs both clusters up and reachable from each other at the same time, and gives no copy to fall back on. Good for a quick proof of concept. |
| GCP disk snapshots shared across projects | Fastest for large disks, but GCP only, and CloudHop would have to create disks and static PVs itself. |
| Kasten K10 | Commercial; no gain over Velero for this. |

Databases are the exception: a volume copy of a running database is crash-consistent at best. Cutover mode (below) stops the workloads first, which makes the copy consistent. A copy taken while a database runs should use the database's own dump or replication instead.

### How Velero moves a volume

1. In the source cluster, a Velero `Backup` with `snapshotMoveData: true` takes a CSI `VolumeSnapshot` of each PVC, mounts it, and uploads the files (with Kopia) to the bucket. The snapshot itself is deleted afterwards; only the bucket copy is kept.
2. In the target cluster, a Velero `Restore` of that backup creates each PVC on the target's storage class and downloads the files into it.

Each cluster needs:

- Velero with its node agent, and the GCP plugin for the bucket (`velero-plugin-for-gcp`).
- A `VolumeSnapshotClass` for the GKE Persistent Disk driver (`pd.csi.storage.gke.io`), labelled `velero.io/csi-volumesnapshot-class: "true"`. GKE clusters have the snapshot CRDs.
- A `BackupStorageLocation` pointing at the move bucket. The target's is read-only (`accessMode: ReadOnly`).

### The bucket

The bucket lives in the **target** account. The source account is the one about to lose its credits, and once its billing stops, anything stored in its project may be unreachable.

- A service account of the target project gets `roles/storage.objectAdmin` on the bucket, and its key is given to Velero in both clusters.
- Backups are deleted after a successful move (Velero `ttl`, e.g. 7 days, so a failed restore can be retried).

## What Velero restores, and what CloudHop applies

Velero only moves the volumes: its `Backup` and `Restore` include only `persistentvolumeclaims` and `persistentvolumes` of the move's namespaces. Everything else is applied by CloudHop from its stored manifests. That keeps CloudHop's rules for what is user-created as the single source of truth, and works the same for objects with no volume.

CloudHop applies the manifests in dependency order: Namespaces, ServiceAccounts and RBAC, ConfigMaps and Secrets, then (after the restore) Services, workloads, Ingresses and the rest.

Manifests need rewriting for a new cluster. Fields to remove or change:

| Kind | Field | Why |
|---|---|---|
| PersistentVolumeClaim | `spec.volumeName`, annotations `pv.kubernetes.io/bind-completed`, `pv.kubernetes.io/bound-by-controller`, `volume.kubernetes.io/selected-node`, `volume.beta.kubernetes.io/storage-provisioner`, `volume.kubernetes.io/storage-provisioner` | Bind the claim to the old cluster's volume; applied elsewhere it would stay Pending forever. Velero handles these when it restores PVCs, but CloudHop must not apply its own copy. |
| PersistentVolumeClaim | `spec.storageClassName` | Mapped to the target's class (see below). |
| Service | `spec.clusterIP`, `spec.clusterIPs` | Allocated by each cluster. `nodePort` values are kept when free, else removed. |
| Anything | provider-specific annotations, e.g. `cloud.google.com/neg-status` | Written by the provider's controllers. Each provider's adapter lists them. |

Today's `_clean_manifest` keeps these; the move step rewrites them when applying, so the stored manifests stay a faithful copy of the source.

### Storage classes

A move has a storage class mapping, e.g. `standard-rwo → standard-rwo`, `premium-rwo → premium-rwo`. CloudHop proposes one from the classes found in each cluster (same name, else the target's default) and the user can change it. It is given to Velero as its `change-storage-class` ConfigMap in the target cluster, and used when rewriting PVC manifests.

## A move, step by step

```
pending → checking → backing_up → [cutover: scaling_down → final_backup] → restoring → applying → verifying → done
                                                        any step ──────→ failed (retryable) | cancelled
```

1. **Checking:** both clusters are reachable, Velero runs in both, both storage locations are `Available`, every source storage class has a mapping, and the target has room (node pool sizes from the cluster sync).
2. **Backing up:** a Velero backup of the namespaces' volumes. The workloads keep running.
3. **Cutover** (cutover mode only, after the user confirms): CloudHop scales the source's Deployments and StatefulSets to 0, recording their replica counts, then takes a final backup. Downtime runs from here to the end of applying.
4. **Restoring:** a Velero restore of the last backup in the target cluster, with the storage class mapping.
5. **Applying:** CloudHop applies the rewritten manifests with server-side apply (`fieldManager: cloudhop`), workloads with the source's original replica counts.
6. **Verifying:** every workload is available and every PVC bound. The source stays scaled down, untouched otherwise, so it can be scaled back up if the move is abandoned.

Modes:

- **Cutover** (default): the steps above. Consistent data, some downtime.
- **Copy**: no scaling down; the target gets a crash-consistent copy while the source keeps running. For testing a move, or stateless workloads.

## Triggering

First version: a **Move** button on a cluster, which asks for the target cluster, the namespaces, the mode and the storage class mapping.

Later: when `spend_status()` for the source account crosses a threshold (e.g. 80%), CloudHop starts steps 1 and 2 on its own, so a recent backup is ready, and notifies the user. The cutover always waits for the user.

## Code

Laid out as in [AGENTS.md](../AGENTS.md); `moves/models.py` becomes a package.

- `moves/models/`:
  - `Move`: source and target cluster, namespaces, mode, storage class mapping, status, the current Velero backup and restore names, replica counts recorded at cutover, error, timestamps.
  - `MoveEvent`: what happened at each step, shown as the move's log in the UI.
- `moves/adapters/`: `MoveAdapter`, one per provider, for what differs between providers: the bucket, Velero's plugin and credentials, the snapshot class, provider-specific annotations to strip.
- `moves/services/`: one function per step, each safe to run again (it checks what is already done first).
- `moves/tasks.py`: a Celery task per step. While a Velero backup or restore runs, the task re-queues itself every 30 seconds and reads its `status.phase`.
- `common/kube/`: `KubeClient` gains `apply()` (server-side apply), `patch()` (for scaling) and `delete()`. Today it can only read.

The `kubernetes` app's resource sync lists native kinds only, so Velero's own objects (CRDs) are never stored or copied.

## Access

Moves are the first part of CloudHop that changes a cluster. The README's "never changes anything" becomes "changes a cluster only during a move the user started".

- Source cluster: scale Deployments and StatefulSets, and create Velero `Backup` objects.
- Target cluster: create objects in the move's namespaces, create Velero `Restore` objects. In practice, `cluster-admin` on the target, or a ClusterRole the Guide provides.
- GCP: the target project's service account for the bucket, as above.

## Costs

- Traffic out of the source region or to another provider is billed to the **source**, so a move uses some of the credits it is trying to save. Checking estimates it from the PVCs' sizes and warns when the budget left looks too small.
- Bucket storage in the target account until the backup expires.

## Open questions

- Should a move also copy cluster-scoped objects the namespaces depend on (ClusterRoles, StorageClasses, PriorityClasses)? Proposed: report them in checking, apply only ClusterRoles and their bindings.
- Ingress and DNS: the target gets new external IPs. CloudHop can show them; updating DNS stays with the user.
- Images in the source project's Artifact Registry stop being pullable once its billing stops. The target needs access to them, or they need copying.

## Milestones

1. Proof of concept: move one namespace with a PVC by hand with Velero between two GKE clusters, to check the data mover and the manifest rewriting.
2. `KubeClient` writes, manifest rewriting, and applying stored manifests to another cluster (no volumes).
3. Velero backup and restore driven by CloudHop, copy mode.
4. Cutover mode, the Move UI and its log.
5. Automatic backups when a budget runs low.
