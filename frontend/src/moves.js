// Shared by the moves list and the move page

export const STEP_LABELS = {
  pending: 'Pending',
  checking: 'Checking',
  scaling_down: 'Scaling down the source',
  backing_up: 'Backing up',
  restoring: 'Restoring',
  copying: 'Copying manifests',
  scaling_up: 'Scaling up the target',
  verifying: 'Verifying',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const FINISHED = ['done', 'failed', 'cancelled']

export const METHOD_LABELS = { velero: 'Velero', manifests: 'Manifests only' }

export function statusTone(status) {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'cancelled') return 'warning'
  return 'info'
}

// Namespaces a move refuses: the cluster's own, GKE's, Velero's and "default"
export function movableNamespace(name) {
  return Boolean(name) && !/^(kube-|gke-|gmp-)/.test(name) && !['default', 'velero'].includes(name)
}
