<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppIcon from '../components/AppIcon.vue'
import { accounts } from '../accounts.js'
import { api } from '../api.js'
import { STEP_LABELS, movableNamespace, statusTone } from '../moves.js'
import { dateTime, timeAgo } from '../format.js'
import { notify } from '../toasts.js'

const route = useRoute()
const router = useRouter()

const moves = ref([])
const loading = ref(false)
const loadError = ref(null)

// Every active cluster of every account, for choosing the source and target
const clusters = ref([])
const showForm = ref(Boolean(route.query.source))
const form = reactive({
  source: route.query.source ? Number(route.query.source) : null,
  target: null,
  namespaces: [],
  mode: 'cutover',
  // Source storage class -> target storage class
  mapping: {},
  storageLocation: 'default',
})
const sourceNamespaces = ref([])
// Storage class of each source PVC, by namespace, from the last resource sync
const claimClasses = ref({})
const loadingSource = ref(false)
const creating = ref(false)
const formError = ref(null)

const byAccount = computed(() =>
  accounts.items
    .map((account) => ({ account, clusters: clusters.value.filter((cluster) => cluster.account_id === account.id) }))
    .filter((group) => group.clusters.length),
)
const storageClasses = computed(() => [
  ...new Set(form.namespaces.flatMap((namespace) => claimClasses.value[namespace] ?? [])),
].sort())
const canCreate = computed(() => form.source && form.target && form.namespaces.length && !creating.value)

loadMoves()
watch(() => accounts.items, loadClusters, { immediate: true })
watch(() => form.source, loadSource, { immediate: true })
watch(storageClasses, (classes) => {
  for (const name of classes) form.mapping[name] ??= name
})

async function loadMoves() {
  loading.value = true
  loadError.value = null
  try {
    moves.value = await api.listMoves()
  } catch (error) {
    loadError.value = error.message
  } finally {
    loading.value = false
  }
}

async function loadClusters() {
  const lists = await Promise.all(
    accounts.items.filter((account) => account.is_valid).map((account) => api.listClusters(account.id).catch(() => [])),
  )
  clusters.value = lists.flat()
}

async function loadSource() {
  form.namespaces = []
  form.mapping = {}
  sourceNamespaces.value = []
  claimClasses.value = {}
  if (!form.source) return
  if (form.target === form.source) form.target = null
  loadingSource.value = true
  try {
    const [summary, claims] = await Promise.all([
      api.resourceSummary(form.source),
      api.listResources(form.source, { kind: 'PersistentVolumeClaim', group: '', limit: 1000 }),
    ])
    sourceNamespaces.value = summary.namespaces.filter(movableNamespace)
    const classes = {}
    for (const claim of claims.results) {
      const name = claim.manifest.spec?.storageClassName
      if (name) (classes[claim.namespace] ??= []).push(name)
    }
    claimClasses.value = classes
  } catch (error) {
    notify(`Could not read the source cluster's namespaces: ${error.message}`, 'error')
  } finally {
    loadingSource.value = false
  }
}

async function createMove() {
  creating.value = true
  formError.value = null
  try {
    const move = await api.createMove({
      source_cluster: form.source,
      target_cluster: form.target,
      namespaces: form.namespaces,
      mode: form.mode,
      storage_class_mapping: Object.fromEntries(storageClasses.value.map((name) => [name, form.mapping[name].trim()])),
      storage_location: form.storageLocation.trim() || 'default',
    })
    notify(`Move ${move.id} started`, 'success')
    router.push({ name: 'move', params: { id: move.id } })
  } catch (error) {
    formError.value = error.message
  } finally {
    creating.value = false
  }
}

function clusterLabel(cluster) {
  return `${cluster.name} · ${cluster.account_name}`
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1>Moves</h1>
        <p class="muted intro">
          Copy namespaces, with their objects and volume data, to a cluster in another account. Velero does the copy
          through a bucket in the target account.
          <RouterLink :to="{ name: 'guide', hash: '#gcp-moves' }">Set up Velero first</RouterLink>
        </p>
      </div>
      <div class="actions">
        <button v-if="!showForm" class="btn btn-primary" @click="showForm = true">
          <AppIcon name="plus" :size="14" />
          New move
        </button>
      </div>
    </header>

    <section v-if="showForm" class="card">
      <div class="section-header">
        <h2>New move</h2>
        <button class="btn btn-sm btn-ghost" @click="showForm = false">Close</button>
      </div>

      <form class="body" @submit.prevent="createMove">
        <div class="pair">
          <div class="field">
            <label for="move-source">From</label>
            <select id="move-source" v-model="form.source" class="select">
              <option :value="null" disabled>Source cluster</option>
              <optgroup v-for="group in byAccount" :key="group.account.id" :label="group.account.name">
                <option v-for="cluster in group.clusters" :key="cluster.id" :value="cluster.id">
                  {{ cluster.name }} ({{ cluster.location }})
                </option>
              </optgroup>
            </select>
          </div>
          <AppIcon name="move" :size="18" class="pair-arrow" />
          <div class="field">
            <label for="move-target">To</label>
            <select id="move-target" v-model="form.target" class="select">
              <option :value="null" disabled>Target cluster</option>
              <optgroup v-for="group in byAccount" :key="group.account.id" :label="group.account.name">
                <option
                  v-for="cluster in group.clusters"
                  :key="cluster.id"
                  :value="cluster.id"
                  :disabled="cluster.id === form.source"
                >
                  {{ cluster.name }} ({{ cluster.location }})
                </option>
              </optgroup>
            </select>
          </div>
        </div>

        <fieldset class="field">
          <legend>Namespaces</legend>
          <div v-if="!form.source" class="muted small">Choose the source cluster first.</div>
          <div v-else-if="loadingSource"><span class="spinner" /></div>
          <div v-else-if="!sourceNamespaces.length" class="muted small">
            No namespaces of yours found. Use <strong>Refresh resources</strong> on the source cluster, then come back.
          </div>
          <div v-else class="choices">
            <label v-for="namespace in sourceNamespaces" :key="namespace" class="choice">
              <input v-model="form.namespaces" type="checkbox" :value="namespace" />
              <span class="mono">{{ namespace }}</span>
              <span v-if="claimClasses[namespace]" class="badge plain">
                {{ claimClasses[namespace].length }} volume{{ claimClasses[namespace].length === 1 ? '' : 's' }}
              </span>
            </label>
          </div>
          <span class="muted small">The target cluster must not have these namespaces yet.</span>
        </fieldset>

        <fieldset class="field">
          <legend>Mode</legend>
          <label class="choice block">
            <input v-model="form.mode" type="radio" value="cutover" />
            <span>
              <strong>Cutover</strong> (recommended): scales the source workloads to 0 before the backup, then starts
              them in the target. The data is consistent; the app is down until the target is ready. The source is
              left scaled down, not deleted.
            </span>
          </label>
          <label class="choice block">
            <input v-model="form.mode" type="radio" value="copy" />
            <span>
              <strong>Copy</strong>: backs up while the source keeps running, so both run afterwards. Volumes of
              running databases may be copied mid-write; use it to try a move.
            </span>
          </label>
        </fieldset>

        <fieldset v-if="storageClasses.length" class="field">
          <legend>Storage classes</legend>
          <span class="muted small">The class each volume gets in the target. Keep the name if the target has it.</span>
          <div v-for="name in storageClasses" :key="name" class="mapping">
            <code>{{ name }}</code>
            <AppIcon name="move" :size="14" />
            <input v-model="form.mapping[name]" class="input mono" spellcheck="false" :aria-label="`Target class for ${name}`" />
          </div>
        </fieldset>

        <details class="advanced">
          <summary>Advanced</summary>
          <div class="field">
            <label for="move-location">Velero storage location</label>
            <input id="move-location" v-model="form.storageLocation" class="input mono" spellcheck="false" />
            <span class="muted small">Its name in both clusters; both must point at the same bucket.</span>
          </div>
        </details>

        <div v-if="formError" class="alert danger">{{ formError }}</div>
        <div class="submit">
          <button class="btn btn-primary" type="submit" :disabled="!canCreate">
            <span v-if="creating" class="spinner" />
            Start move
          </button>
        </div>
      </form>
    </section>

    <section class="card">
      <div class="section-header">
        <h2>All moves</h2>
        <button class="btn btn-sm" :disabled="loading" @click="loadMoves">
          <AppIcon name="refresh" :size="14" />
          Refresh
        </button>
      </div>

      <div v-if="loading && !moves.length" class="empty"><span class="spinner" /></div>
      <div v-else-if="loadError" class="body"><div class="alert danger">{{ loadError }}</div></div>
      <div v-else-if="!moves.length" class="empty">
        <AppIcon name="move" :size="32" />
        <h3>No moves yet</h3>
        <p>Use <strong>New move</strong>, or <strong>Move namespaces</strong> on a cluster.</p>
      </div>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Move</th>
              <th>From</th>
              <th>To</th>
              <th>Namespaces</th>
              <th>Mode</th>
              <th>Status</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="move in moves"
              :key="move.id"
              class="clickable"
              @click="router.push({ name: 'move', params: { id: move.id } })"
            >
              <td>
                <RouterLink :to="{ name: 'move', params: { id: move.id } }" @click.stop>#{{ move.id }}</RouterLink>
              </td>
              <td>{{ clusterLabel(move.source_cluster) }}</td>
              <td>{{ clusterLabel(move.target_cluster) }}</td>
              <td class="mono">{{ move.namespaces.join(', ') }}</td>
              <td>{{ move.mode }}</td>
              <td><span class="badge" :class="statusTone(move.status)">{{ STEP_LABELS[move.status] }}</span></td>
              <td :title="dateTime(move.created_at)">{{ timeAgo(move.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.intro {
  max-width: 640px;
  margin-top: 4px;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 16px 20px 20px;
}

.pair {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
}

.pair .field {
  flex: 1;
  min-width: 220px;
}

.pair-arrow {
  margin-bottom: 8px;
  color: var(--text-muted);
}

fieldset {
  margin: 0;
  padding: 0;
  border: none;
}

legend {
  margin-bottom: 6px;
  padding: 0;
  font-weight: 500;
}

.choices {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
}

.choice {
  display: flex;
  gap: 8px;
  align-items: center;
}

.choice.block {
  align-items: flex-start;
  max-width: 720px;
}

.choice.block input {
  margin-top: 4px;
}

.mapping {
  display: flex;
  gap: 10px;
  align-items: center;
  max-width: 480px;
  color: var(--text-muted);
}

.mapping code {
  min-width: 120px;
  color: var(--text);
}

.mapping .input {
  flex: 1;
  min-width: 0;
}

.advanced summary {
  color: var(--text-muted);
  cursor: pointer;
}

.advanced .field {
  max-width: 360px;
  margin-top: 10px;
}

.small {
  font-size: 12.5px;
}

.submit {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .pair-arrow {
    display: none;
  }
}
</style>
