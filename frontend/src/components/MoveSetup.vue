<script setup>
// What a move between two clusters needs set up, as steps filled in for those clusters,
// each checked against them by the API
import { computed, ref, watch } from 'vue'

import AppIcon from '/src/components/AppIcon.vue'
import CodeBlock from '/src/components/CodeBlock.vue'
import { accounts } from '/src/accounts.js'
import { api } from '/src/api.js'
import {
  READ_ONLY_LOCATION,
  SNAPSHOT_CLASS,
  VELERO_VERSION,
  bucketCommands,
  getCredentials,
  kubectlApply,
  manifestTargetRole,
  moverRole,
  regionOf,
  scalerRole,
  secretReaderRole,
  veleroCli,
  veleroInstall,
} from '/src/moveSetup.js'

const props = defineProps({
  // Clusters as the clusters API lists them
  source: { type: Object, required: true },
  target: { type: Object, required: true },
  method: { type: String, required: true },
  mode: { type: String, required: true },
  storageLocation: { type: String, default: 'default' },
})

const STATES = {
  done: { label: 'Done', tone: 'success', icon: 'check' },
  todo: { label: 'To do', tone: 'danger', icon: 'x' },
  warning: { label: 'Check this', tone: 'warning', icon: 'alert' },
  unknown: { label: 'Cannot tell', tone: 'plain', icon: 'alert' },
  manual: { label: 'Not checked', tone: 'plain', icon: null },
  marked: { label: 'Marked done', tone: 'success', icon: 'check' },
}
// Where each step's commands run, or what they set up
const SCOPES = {
  local: 'Local machine',
  source: 'Source cluster',
  account: 'Destination account',
  target: 'Destination cluster',
}
const CHECK_STATES = { ok: 'done', missing: 'todo', warning: 'warning', unknown: 'unknown' }
const DONE = ['done', 'marked']

// Steps CloudHop cannot check (on the user's machine), ticked off by the user; remembered in this
// browser only, and the same for every move
const MARKED_KEY = 'cloudhop.moveSetup.marked'
const marked = ref(readMarked())

function readMarked() {
  try {
    return JSON.parse(localStorage.getItem(MARKED_KEY)) ?? {}
  } catch {
    return {}
  }
}

function toggleMarked(id) {
  marked.value = { ...marked.value, [id]: !marked.value[id] }
  try {
    localStorage.setItem(MARKED_KEY, JSON.stringify(marked.value))
  } catch {
    // Storage blocked: the mark lasts until the page is left
  }
}

const checks = ref([])
const checking = ref(false)
const checkError = ref(null)
const bucket = ref('')
// The target account's service account, which Velero reuses; the check reads it from CloudHop's key
const identity = ref('')
// Answers to an earlier pair of clusters are dropped
let latest = 0

const sourceAccount = computed(() => accounts.find(props.source.account_id))
const targetAccount = computed(() => accounts.find(props.target.account_id))
const gcp = computed(() => sourceAccount.value?.provider === 'gcp' && targetAccount.value?.provider === 'gcp')
const velero = computed(() => props.method === 'velero')
const cutover = computed(() => props.mode === 'cutover')

function side(cluster, account) {
  return {
    cluster: cluster.name,
    location: cluster.location,
    project: account?.project_id || 'PROJECT_ID',
    uniqueId: account?.external_id || 'SERVICE_ACCOUNT_UNIQUE_ID',
    accountName: account?.name ?? '',
  }
}

// Account, project and location, without repeating an account named after its project
function where({ accountName, project, location }, withLocation = true) {
  return [...new Set([accountName, project])].filter(Boolean).concat(withLocation ? [location] : []).join(' · ')
}
const src = computed(() => side(props.source, sourceAccount.value))
const dst = computed(() => side(props.target, targetAccount.value))
// Until the check answers, the name CloudHop's service account gets in the Guide
const member = computed(() => identity.value || `cloudhop@${dst.value.project}.iam.gserviceaccount.com`)

const steps = computed(() => (velero.value ? veleroSteps() : manifestSteps()).map((step) => ({
  ...step,
  results: step.checks.map(([key, cluster]) => checks.value.find((c) => c.key === key && c.cluster === cluster))
    .filter(Boolean),
})).map((step) => ({ ...step, state: stepState(step) })))

const doneCount = computed(() => steps.value.filter((step) => DONE.includes(step.state)).length)
const unreachable = computed(() => checks.value.filter((check) => check.key === 'connect'))

function veleroSteps() {
  const s = src.value
  const t = dst.value
  const scale = cutover.value ? ', and scale workloads down' : ''
  return [
    {
      id: 'tools',
      scope: 'local',
      title: 'Install the Velero CLI on your machine',
      text: 'It installs Velero into each cluster below. You also need gcloud, signed in with access to both '
        + 'projects, and kubectl. CloudHop cannot see your machine: check that velero version --client-only '
        + `prints ${VELERO_VERSION}, then mark this step done. CloudHop checks the version it installed in each `
        + 'cluster below.',
      code: veleroCli(),
      checks: [],
    },
    {
      id: 'bucket',
      scope: 'account',
      title: 'Create the bucket in the target account',
      where: where(t, false),
      text: 'Velero in the source writes the backup to this Cloud Storage bucket, and Velero in the target reads '
        + 'it. It belongs to the target account, which keeps its billing after the source\'s stops. Velero '
        + `reuses CloudHop's service account in this account (${member.value}) and its key, which ends up as a `
        + 'Secret in both clusters: anyone who can read Secrets there can use it.',
      bucketInput: true,
      code: bucketCommands({
        project: t.project, bucket: bucket.value, region: regionOf(t.location), member: member.value,
      }),
      checks: [['bucket', ''], ['bucket_access', '']],
    },
    {
      id: 'source',
      scope: 'source',
      title: `Set up the source cluster ${s.cluster}`,
      where: where(s),
      text: `Install Velero in this cluster, storing its backups in the destination's bucket; let it snapshot `
        + `volumes; and allow CloudHop to start backups${scale}. `
        + `Set KEY to the key of ${t.accountName}'s CloudHop service account: the target's, not the source's.`,
      code: [
        `# Point kubectl at the source cluster\n${getCredentials(s)}`,
        keyLine(),
        `# Install Velero in this cluster, the source: it backs the namespaces up here and stores the backup\n`
          + `# in gs://${bucket.value}, the bucket in the destination account (${t.project})\n`
          + veleroInstall({ bucket: bucket.value, secretFile: '"$KEY"' }),
        `# Velero copies volumes through CSI snapshots taken with this class\n${kubectlApply(SNAPSHOT_CLASS)}`,
        `# CloudHop's access, bound to ${s.accountName}'s service account\n${kubectlApply(moverRole(s.uniqueId))}`,
        '# Should say Available\nvelero backup-location get',
      ].join('\n\n'),
      checks: [['velero', 'source'], ['storage_location', 'source'], ['snapshot_class', 'source'],
        ['access', 'source']],
    },
    {
      id: 'target',
      scope: 'target',
      title: `Set up the target cluster ${t.cluster}`,
      where: where(t),
      text: 'Install Velero on the same bucket, read-only and looking for new backups every 10 seconds, and '
        + 'allow CloudHop to start restores'
        + `${cutover.value ? ' and scale workloads up' : ''}, with the same key as the source.`,
      code: [
        `# Point kubectl at the target cluster\n${getCredentials(t)}`,
        keyLine(),
        `# Install Velero in this cluster, the destination: it finds the source's backup in the same\n`
          + `# bucket, gs://${bucket.value}, and restores it here\n`
          + veleroInstall({ bucket: bucket.value, secretFile: '"$KEY"' }),
        `# Read the backups the source writes, never write to the bucket, and look for new ones every 10s\n`
          + READ_ONLY_LOCATION,
        `# CloudHop's access, bound to ${t.accountName}'s service account\n${kubectlApply(moverRole(t.uniqueId))}`,
        '# Should say Available\nvelero backup-location get',
      ].join('\n\n'),
      checks: [['velero', 'target'], ['storage_location', 'target'], ['access', 'target']],
    },
  ]
}

// The same key in both clusters: the target account's, which owns the bucket
function keyLine() {
  return `# The key of ${dst.value.accountName}'s CloudHop service account, ${member.value}\nKEY=./cloudhop-key.json`
}

function manifestSteps() {
  const s = src.value
  const t = dst.value
  const sourceCode = [
    `# Point kubectl at the source cluster\n${getCredentials(s)}`,
    `# Read Secrets too; already done if you set it up when adding ${s.accountName}\n`
      + kubectlApply(secretReaderRole(s.uniqueId)),
  ]
  if (cutover.value) sourceCode.push(kubectlApply(scalerRole(s.uniqueId)))
  return [
    {
      id: 'source',
      scope: 'source',
      title: `Let CloudHop read the source cluster ${s.cluster}`,
      where: where(s),
      text: 'CloudHop reads every object in the namespaces, Secrets included'
        + `${cutover.value ? ', and scales the workloads to 0 once the target runs them' : ''}.`,
      code: sourceCode.join('\n\n'),
      checks: [['access', 'source']],
    },
    {
      id: 'target',
      scope: 'target',
      title: `Let CloudHop write in the target cluster ${t.cluster}`,
      where: where(t),
      text: 'CloudHop creates the namespaces and applies their objects, with the built-in admin role. A namespace '
        + 'with ResourceQuotas or LimitRanges needs cluster-admin instead.',
      code: [
        `# Point kubectl at the target cluster\n${getCredentials(t)}`,
        kubectlApply(manifestTargetRole(t.uniqueId)),
      ].join('\n\n'),
      checks: [['access', 'target']],
    },
  ]
}

function stepState(step) {
  if (!step.checks.length) return marked.value[step.id] ? 'marked' : 'manual'
  if (unreachable.value.some((check) => step.checks.some(([, cluster]) => cluster === check.cluster))) {
    return 'unknown'
  }
  if (step.results.length < step.checks.length) return 'unknown'
  const states = step.results.map((check) => CHECK_STATES[check.status])
  return ['todo', 'unknown', 'warning'].find((state) => states.includes(state)) ?? 'done'
}

async function check() {
  const request = ++latest
  checking.value = true
  checkError.value = null
  try {
    const result = await api.checkMoveSetup({
      source_cluster: props.source.id,
      target_cluster: props.target.id,
      method: props.method,
      mode: props.mode,
      storage_location: props.storageLocation.trim() || 'default',
      bucket: velero.value ? bucket.value : undefined,
    })
    if (request !== latest) return
    checks.value = result.checks
    // The bucket Velero already uses wins over the suggested name
    if (result.bucket) bucket.value = result.bucket
    if (result.identity) identity.value = result.identity
  } catch (error) {
    if (request === latest) checkError.value = error.message
  } finally {
    if (request === latest) checking.value = false
  }
}

watch(
  () => [props.source.id, props.target.id, props.method, props.mode],
  () => {
    checks.value = []
    identity.value = ''
    // Bucket names are global; the target's project ID keeps this one free
    bucket.value = `cloudhop-moves-${dst.value.project}`
    if (gcp.value) check()
  },
  { immediate: true },
)
</script>

<template>
  <div class="move-setup">
    <div class="head">
      <div>
        <strong>Before you start</strong>
        <span v-if="gcp && !checking && !checkError" class="muted">
          · {{ doneCount }} of {{ steps.length }} steps done
        </span>
      </div>
      <button v-if="gcp" type="button" class="btn btn-sm" :disabled="checking" @click="check">
        <span v-if="checking" class="spinner" />
        <AppIcon v-else name="refresh" :size="14" />
        Check again
      </button>
    </div>

    <div v-if="!gcp" class="alert warning">
      Moves run between Google Cloud clusters only for now.
    </div>
    <template v-else>
      <p class="muted small">
        What this move needs in {{ src.cluster }} and {{ dst.cluster }}, with your projects and service accounts
        filled in. Run each step's commands, then <strong>Check again</strong>. The move checks everything once more
        when it starts. <RouterLink :to="{ name: 'guide', hash: '#gcp-moves' }">More in the Guide</RouterLink>
      </p>

      <div v-if="checkError" class="alert danger">Could not check the setup: {{ checkError }}</div>
      <div v-for="check in unreachable" :key="check.cluster" class="alert danger">{{ check.detail }}</div>

      <ol class="steps">
        <li v-for="(step, index) in steps" :key="step.id" class="step" :class="step.state">
          <div class="step-head">
            <span class="marker" :class="STATES[step.state].tone">
              <span v-if="checking && step.checks.length" class="spinner" />
              <AppIcon v-else-if="STATES[step.state].icon" :name="STATES[step.state].icon" :size="13" />
              <template v-else>{{ index + 1 }}</template>
            </span>
            <div class="title">
              <strong>{{ step.title }}</strong>
              <span v-if="step.where" class="muted small">{{ step.where }}</span>
              <label v-if="!step.checks.length" class="mark small">
                <input type="checkbox" :checked="Boolean(marked[step.id])" @change="toggleMarked(step.id)" />
                Mark done
              </label>
            </div>
            <div class="tags">
              <span class="badge plain scope" :class="step.scope">{{ SCOPES[step.scope] }}</span>
              <span v-if="!checking" class="badge" :class="STATES[step.state].tone">
                {{ STATES[step.state].label }}
              </span>
            </div>
          </div>

          <ul v-if="step.results.length" class="results">
            <li v-for="result in step.results" :key="result.key" :class="CHECK_STATES[result.status]">
              <AppIcon :name="STATES[CHECK_STATES[result.status]].icon" :size="13" />
              <span>{{ result.detail }}</span>
            </li>
          </ul>

          <details class="how" :open="!DONE.includes(step.state)">
            <summary>{{ DONE.includes(step.state) ? 'Show the commands' : 'How' }}</summary>
            <p class="small">{{ step.text }}</p>
            <div v-if="step.bucketInput" class="field bucket">
              <label :for="`bucket-${step.id}`">Bucket name</label>
              <input
                :id="`bucket-${step.id}`"
                v-model.trim="bucket"
                class="input mono"
                spellcheck="false"
                @change="check"
              />
              <span class="muted small">Checked when you leave the field.</span>
            </div>
            <CodeBlock :code="step.code" label="shell" />
          </details>
        </li>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.move-setup {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-muted);
}

.head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.small {
  font-size: 12.5px;
}

p {
  margin: 0;
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.step {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
}

.step-head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.mark {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  width: fit-content;
  margin-top: 4px;
  color: var(--text-muted);
  cursor: pointer;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}

.scope.local {
  background: var(--scope-local-soft);
  color: var(--scope-local);
}

.scope.source {
  background: var(--scope-source-soft);
  color: var(--scope-source);
}

/* The bucket lives in the destination's account, so it shares the destination's color */
.scope.account,
.scope.target {
  background: var(--scope-target-soft);
  color: var(--scope-target);
}

.marker {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
}

.marker.success {
  background: var(--success-soft);
  color: var(--success);
}

.marker.danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.marker.warning {
  background: var(--warning-soft);
  color: var(--warning);
}

.marker .spinner {
  width: 12px;
  height: 12px;
}

.title {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  overflow-wrap: anywhere;
}

.results {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0 0 0 32px;
  padding: 0;
  font-size: 12.5px;
  list-style: none;
}

.results li {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  color: var(--text-muted);
}

.results li svg {
  flex: none;
  margin-top: 2px;
}

.results .done svg {
  color: var(--success);
}

.results .todo {
  color: var(--danger);
}

.results .warning {
  color: var(--warning);
}

.how {
  min-width: 0;
  margin-left: 32px;
}

.how summary {
  color: var(--text-muted);
  font-size: 12.5px;
  cursor: pointer;
}

.how > * + * {
  margin-top: 8px;
}

.bucket {
  max-width: 360px;
}

@media (max-width: 640px) {
  .results,
  .how {
    margin-left: 0;
  }
}
</style>
