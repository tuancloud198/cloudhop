<script setup>
import { computed, ref, watch } from 'vue'

import AppIcon from '../components/AppIcon.vue'
import ResourceDrawer from '../components/ResourceDrawer.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { accounts } from '../accounts.js'
import { api } from '../api.js'
import { apiVersion, dateTime, timeAgo } from '../format.js'
import { notify } from '../toasts.js'

const PAGE_SIZE = 50
// Value of the namespace filter that selects cluster-scoped objects (stored with namespace "")
const CLUSTER_SCOPED = ''

const props = defineProps({ id: { type: String, required: true } })

const cluster = ref(null)
const clusterError = ref(null)
const showDetails = ref(false)

const summary = ref({ kinds: [], namespaces: [], synced_at: null })
const resources = ref([])
const total = ref(0)
const loadingResources = ref(false)
const resourcesError = ref(null)
const syncing = ref(false)
// Kinds the last sync could not read, e.g. Secrets without RBAC access
const skipped = ref([])
const selected = ref(null)

// Filters; kind is "group|Kind" or null
const kind = ref(null)
const namespace = ref(null)
const search = ref('')
const offset = ref(0)
// Ignore responses of requests that a newer one replaced (filters typed quickly).
// Declared before the immediate watcher below, which already calls loadResources
let latestRequest = 0
let searchTimer

const account = computed(() => cluster.value && accounts.find(cluster.value.account_id))
const totalStored = computed(() => summary.value.kinds.reduce((sum, item) => sum + item.count, 0))
const hasFilters = computed(() => kind.value !== null || namespace.value !== null || search.value !== '')
const pageEnd = computed(() => Math.min(offset.value + PAGE_SIZE, total.value))

watch(
  () => props.id,
  () => {
    cluster.value = null
    clusterError.value = null
    skipped.value = []
    selected.value = null
    clearFilters()
    loadCluster()
    refreshResources()
  },
  { immediate: true },
)

watch([kind, namespace], () => {
  offset.value = 0
  loadResources()
})

watch(search, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    offset.value = 0
    loadResources()
  }, 300)
})

async function loadCluster() {
  try {
    cluster.value = await api.getCluster(props.id)
  } catch (error) {
    clusterError.value = error.status === 404 ? 'Cluster not found.' : error.message
  }
}

async function loadSummary() {
  try {
    summary.value = await api.resourceSummary(props.id)
  } catch {
    // The list below reports the error
  }
}

async function loadResources() {
  const request = ++latestRequest
  loadingResources.value = true
  resourcesError.value = null
  const [group, kindName] = kind.value ? kind.value.split('|') : [undefined, undefined]
  try {
    const page = await api.listResources(props.id, {
      limit: PAGE_SIZE,
      offset: offset.value,
      group,
      kind: kindName,
      namespace: namespace.value ?? undefined,
      search: search.value || undefined,
    })
    if (request !== latestRequest) return
    resources.value = page.results
    total.value = page.count
  } catch (error) {
    if (request !== latestRequest) return
    resourcesError.value = error.message
  } finally {
    if (request === latestRequest) loadingResources.value = false
  }
}

function refreshResources() {
  loadSummary()
  loadResources()
}

async function syncResources() {
  syncing.value = true
  try {
    const result = await api.syncResources(props.id)
    skipped.value = result.skipped
    const count = Object.values(result.synced).reduce((sum, value) => sum + value, 0)
    notify(`Synced ${count} resource${count === 1 ? '' : 's'} from ${cluster.value?.name ?? 'the cluster'}`, 'success')
    offset.value = 0
    refreshResources()
  } catch (error) {
    notify(`Resource sync failed: ${error.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

function clearFilters() {
  kind.value = null
  namespace.value = null
  search.value = ''
  offset.value = 0
}

function goToPage(newOffset) {
  offset.value = newOffset
  loadResources()
}

function memory(mb) {
  return mb ? `${Math.round(mb / 1024)} GB` : '–'
}
</script>

<template>
  <div class="page">
    <div v-if="clusterError" class="card empty">
      <h3>{{ clusterError }}</h3>
      <RouterLink to="/">Back to start</RouterLink>
    </div>

    <template v-else>
      <header class="page-header">
        <div>
          <div class="breadcrumb">
            <RouterLink v-if="cluster" :to="{ name: 'account', params: { id: cluster.account_id } }">
              {{ account?.name ?? `Account ${cluster.account_id}` }}
            </RouterLink>
            <span> / Cluster</span>
          </div>
          <h1>{{ cluster?.name ?? '…' }}</h1>
          <div v-if="cluster" class="meta">
            <StatusBadge :status="cluster.status" />
            <span v-if="!cluster.is_active" class="badge warning">no longer in provider</span>
            <span>{{ cluster.location }}</span>
            <span>Kubernetes <code>{{ cluster.kubernetes_version }}</code></span>
            <span>{{ cluster.spec.node_count ?? 0 }} nodes</span>
            <button class="link" @click="showDetails = !showDetails">
              {{ showDetails ? 'Hide details' : 'Show details' }}
            </button>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-primary" :disabled="syncing || !cluster?.is_active" @click="syncResources">
            <span v-if="syncing" class="spinner" />
            <AppIcon v-else name="refresh" :size="14" />
            {{ syncing ? 'Syncing resources…' : 'Refresh resources' }}
          </button>
        </div>
      </header>

      <section v-if="cluster && showDetails" class="card details">
        <dl class="facts">
          <dt>Endpoint</dt>
          <dd class="mono">{{ cluster.spec.endpoint || '–' }}</dd>
          <dt>Network</dt>
          <dd class="mono">{{ cluster.spec.network || '–' }} / {{ cluster.spec.subnetwork || '–' }}</dd>
          <dt>Release channel</dt>
          <dd>{{ cluster.spec.release_channel || '–' }}</dd>
          <dt>Mode</dt>
          <dd>{{ cluster.spec.autopilot ? 'Autopilot' : 'Standard' }}</dd>
        </dl>
        <div v-if="cluster.spec.node_pools?.length" class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Node pool</th>
                <th>Machine type</th>
                <th>Nodes</th>
                <th>CPU / node</th>
                <th>Memory / node</th>
                <th>Disk / node</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="pool in cluster.spec.node_pools" :key="pool.name">
                <td>{{ pool.name }}</td>
                <td class="mono">{{ pool.machine_type }}</td>
                <td>{{ pool.node_count ?? '–' }}</td>
                <td>{{ pool.cpu ?? '–' }}</td>
                <td>{{ memory(pool.memory_mb) }}</td>
                <td>{{ pool.disk_size_gb ? `${pool.disk_size_gb} GB ${pool.disk_type ?? ''}` : '–' }}</td>
                <td class="mono">{{ pool.version }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div v-if="skipped.length" class="alert warning">
        <AppIcon name="alert" />
        <div>
          <strong>{{ skipped.length }} kind{{ skipped.length === 1 ? '' : 's' }} could not be read and were not stored:</strong>
          <ul>
            <li v-for="item in skipped" :key="`${item.group}|${item.kind}`">
              <strong>{{ item.kind }}</strong> — {{ item.reason }}
            </li>
          </ul>
        </div>
      </div>

      <section class="card">
        <div class="section-header">
          <div>
            <h2>Resources</h2>
            <span class="muted small">
              User-created native objects ·
              <template v-if="summary.synced_at">
                <span :title="dateTime(summary.synced_at)">synced {{ timeAgo(summary.synced_at) }}</span>
              </template>
              <template v-else>never synced</template>
            </span>
          </div>
          <div class="filters">
            <select v-model="kind" class="select" aria-label="Kind">
              <option :value="null">All kinds ({{ totalStored }})</option>
              <option v-for="item in summary.kinds" :key="`${item.group}|${item.kind}`" :value="`${item.group}|${item.kind}`">
                {{ item.kind }}{{ item.group ? ` (${item.group})` : '' }} · {{ item.count }}
              </option>
            </select>
            <select v-model="namespace" class="select" aria-label="Namespace">
              <option :value="null">All namespaces</option>
              <option :value="CLUSTER_SCOPED">Cluster-scoped</option>
              <option v-for="name in summary.namespaces" :key="name" :value="name">{{ name }}</option>
            </select>
            <div class="search">
              <AppIcon name="search" :size="14" />
              <input v-model="search" class="input" type="search" placeholder="Search name" aria-label="Search name" />
            </div>
          </div>
        </div>

        <div v-if="resourcesError" class="empty">
          <div class="alert danger">{{ resourcesError }}</div>
        </div>
        <div v-else-if="loadingResources && !resources.length" class="empty"><span class="spinner" /></div>
        <div v-else-if="!resources.length" class="empty">
          <AppIcon name="box" :size="32" />
          <template v-if="hasFilters">
            <h3>No resources match</h3>
            <button class="btn btn-sm" @click="clearFilters">Clear filters</button>
          </template>
          <template v-else>
            <h3>No resources stored</h3>
            <p>Use <strong>Refresh resources</strong> to read the objects in this cluster.</p>
          </template>
        </div>

        <template v-else>
          <div class="table-wrap" :class="{ dim: loadingResources }">
            <table class="table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Name</th>
                  <th>Namespace</th>
                  <th>API version</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="resource in resources" :key="resource.id" class="clickable" @click="selected = resource">
                  <td><span class="badge plain">{{ resource.kind }}</span></td>
                  <td class="mono name">{{ resource.name }}</td>
                  <td>
                    <span v-if="resource.namespace" class="mono">{{ resource.namespace }}</span>
                    <span v-else class="muted">–</span>
                  </td>
                  <td class="mono muted">{{ apiVersion(resource) }}</td>
                  <td :title="dateTime(resource.kube_created_at)">{{ timeAgo(resource.kube_created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="pager">
            <span class="muted">{{ offset + 1 }}–{{ pageEnd }} of {{ total }}</span>
            <button class="btn btn-sm" :disabled="offset === 0" @click="goToPage(Math.max(0, offset - PAGE_SIZE))">
              Previous
            </button>
            <button class="btn btn-sm" :disabled="pageEnd >= total" @click="goToPage(offset + PAGE_SIZE)">
              Next
            </button>
          </div>
        </template>
      </section>
    </template>

    <ResourceDrawer :resource="selected" @close="selected = null" />
  </div>
</template>

<style scoped>
.link {
  padding: 0;
  border: none;
  background: none;
  color: var(--primary);
  font: inherit;
  cursor: pointer;
}

.details {
  display: flex;
  flex-direction: column;
}

.facts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 20px;
  margin: 0;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}

.facts dt {
  color: var(--text-muted);
}

.facts dd {
  margin: 0;
  word-break: break-all;
}

.alert ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.small {
  font-size: 12.5px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.search {
  position: relative;
  display: flex;
  align-items: center;
}

.search svg {
  position: absolute;
  left: 10px;
  color: var(--text-muted);
}

.search .input {
  width: 200px;
  padding-left: 30px;
}

.name {
  font-weight: 500;
}

.dim {
  opacity: 0.6;
  transition: opacity 0.15s;
}

.pager {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
  padding: 10px 16px;
  border-top: 1px solid var(--border);
}

.pager .muted {
  margin-right: auto;
}
</style>
