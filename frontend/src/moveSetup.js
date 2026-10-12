// Commands and manifests that set up moves, shared by the Guide (with placeholders) and the
// new-move form (filled in for the two clusters chosen)

// The GCP plugin's version has to match Velero's (plugin 1.14 for Velero 1.18):
// https://github.com/vmware-tanzu/velero-plugin-for-gcp#compatibility
export const VELERO_VERSION = 'v1.18.4'
export const GCP_PLUGIN_VERSION = 'v1.14.4'

// GKE locations are a region or a zone; a bucket goes in a region
export function regionOf(location) {
  return /-[a-z]$/.test(location) ? location.slice(0, -2) : location
}

export function veleroCli(version = VELERO_VERSION) {
  return `# The Velero CLI installs the same version of Velero into the clusters, so keep it
# matching the GCP plugin's version below. Linux or macOS:
VERSION=${version}
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
curl -fsSL "https://github.com/vmware-tanzu/velero/releases/download/$VERSION/velero-$VERSION-$OS-$ARCH.tar.gz" | tar -xz
sudo install "velero-$VERSION-$OS-$ARCH/velero" /usr/local/bin/

# Should print $VERSION. Another velero earlier on your PATH would install its own version:
# which -a velero
velero version --client-only
# Show CSI snapshots in velero backup describe
velero client config set features=EnableCSI
gcloud version && kubectl version --client`
}

export function getCredentials({ cluster, location, project }) {
  return `gcloud container clusters get-credentials ${cluster} \\
  --location ${location} \\
  --project ${project}`
}

// Feeds a manifest to kubectl without a file, so a step is one paste
export function kubectlApply(manifest) {
  return `kubectl apply -f - <<'EOF'
${manifest.trimEnd()}
EOF`
}

// Velero reuses the target account's CloudHop service account, and its key, for the bucket.
// member is that account's email.
export function bucketCommands({ project, bucket, region, member }) {
  return `gcloud storage buckets create gs://${bucket} \\
  --project ${project} \\
  --location ${region} \\
  --uniform-bucket-level-access

# Velero writes and reads the backups as this account
gcloud storage buckets add-iam-policy-binding gs://${bucket} \\
  --member serviceAccount:${member} \\
  --role roles/storage.objectAdmin

# CloudHop checks the bucket as this account
gcloud storage buckets add-iam-policy-binding gs://${bucket} \\
  --member serviceAccount:${member} \\
  --role roles/storage.legacyBucketReader`
}

// secretFile: the target account's CloudHop key, the same in both clusters
export function veleroInstall({ bucket, secretFile, pluginVersion = GCP_PLUGIN_VERSION }) {
  return `velero install \\
  --provider gcp \\
  --plugins velero/velero-plugin-for-gcp:${pluginVersion} \\
  --bucket ${bucket} \\
  --secret-file ${secretFile} \\
  --features=EnableCSI \\
  --use-node-agent \\
  --use-volume-snapshots=false`
}

// Velero only snapshots volumes through CSI, and so copies their data, with --features=EnableCSI:
// https://velero.io/docs/main/csi/

// The target only reads the backups the source wrote, and looks for new ones every 10 seconds
// instead of every minute, so a move's restore starts sooner
export const READ_ONLY_LOCATION = `kubectl -n velero patch backupstoragelocation default \\
  --type merge -p '{"spec":{"accessMode":"ReadOnly","backupSyncPeriod":"10s"}}'`

export const SNAPSHOT_CLASS = `apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: velero-pd
  labels:
    # Velero takes CSI snapshots with the class carrying this label
    velero.io/csi-volumesnapshot-class: "true"
driver: pd.csi.storage.gke.io
# The snapshot is only needed until Velero has copied it to the bucket
deletionPolicy: Delete
`

// RBAC subjects are the service account's numeric unique ID, not its email
function binding(kind, name, uniqueId, role = name, namespace = null) {
  return `apiVersion: rbac.authorization.k8s.io/v1
kind: ${kind}
metadata:
  name: ${name}${namespace ? `\n  namespace: ${namespace}` : ''}
subjects:
  - kind: User
    name: "${uniqueId}"
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: ${kind === 'RoleBinding' ? 'Role' : 'ClusterRole'}
  name: ${role}
  apiGroup: rbac.authorization.k8s.io
`
}

export function secretReaderRole(uniqueId) {
  return `apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cloudhop-secret-reader
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list"]
---
${binding('ClusterRoleBinding', 'cloudhop-secret-reader', uniqueId)}`
}

// Manifests-only moves: the target creates the namespaces and applies every object in them
export function manifestTargetRole(uniqueId) {
  return `# Target cluster: create the moved namespaces and apply their objects
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cloudhop-manifest-mover
rules:
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "create", "patch"]
---
${binding('ClusterRoleBinding', 'cloudhop-manifest-mover', uniqueId)}---
# Kubernetes' built-in "admin" role: create and update the objects inside namespaces
${binding('ClusterRoleBinding', 'cloudhop-manifest-admin', uniqueId, 'admin')}`
}

export function scalerRole(uniqueId) {
  return `# Source cluster, cutover only: scale the workloads to 0 once the target runs them
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cloudhop-scaler
rules:
  - apiGroups: ["apps"]
    resources: ["deployments/scale", "statefulsets/scale"]
    verbs: ["get", "patch"]
---
${binding('ClusterRoleBinding', 'cloudhop-scaler', uniqueId)}`
}

// Velero moves, in both clusters
export function moverRole(uniqueId) {
  return `apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cloudhop-mover
rules:
  - apiGroups: ["velero.io"]
    resources: ["backups", "restores"]
    verbs: ["get", "list", "create"]
  - apiGroups: ["velero.io"]
    resources: ["backupstoragelocations"]
    verbs: ["get", "list"]
  - apiGroups: ["snapshot.storage.k8s.io"]
    resources: ["volumesnapshotclasses"]
    verbs: ["get", "list"]
  # Cutover: scale the source down and the target up
  - apiGroups: ["apps"]
    resources: ["deployments/scale", "statefulsets/scale"]
    verbs: ["get", "patch"]
---
${binding('ClusterRoleBinding', 'cloudhop-mover', uniqueId)}---
# In Velero's namespace only: storage class renames for its restores, and reading its install
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: cloudhop-mover
  namespace: velero
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "create", "patch"]
  # Check Velero's version, CSI support and plugin
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get"]
  # Tell why a volume copy is stuck, and cancel one that cannot go on
  - apiGroups: ["velero.io"]
    resources: ["datauploads", "datadownloads"]
    verbs: ["get", "list", "patch"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["list"]
---
${binding('RoleBinding', 'cloudhop-mover', uniqueId, 'cloudhop-mover', 'velero')}`
}
