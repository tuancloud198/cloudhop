<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppIcon from '../components/AppIcon.vue'
import BillingCard from '../components/BillingCard.vue'
import ProviderLogo from '../components/ProviderLogo.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { accounts } from '../accounts.js'
import { api } from '../api.js'
import { PROVIDERS, dateTime, timeAgo } from '../format.js'
import { notify } from '../toasts.js'

const props = defineProps({ id: { type: String, required: true } })
const router = useRouter()

const account = computed(() => accounts.find(props.id))
const clusters = ref([])
const loading = ref(false)
const loadError = ref(null)
const syncing = ref(false)
const deleting = ref(false)

// Every cluster sync updates all active clusters, so the newest update is the last sync
const lastSynced = computed(() =>
  clusters.value.reduce((latest, cluster) => (cluster.updated_at > latest ? cluster.updated_at : latest), ''),
)

watch(() => props.id, loadClusters, { immediate: true })

async function loadClusters() {
  loading.value = true
  loadError.value = null
  clusters.value = []
  try {
    clusters.value = await api.listClusters(props.id)
  } catch (error) {
    loadError.value = error.message
  } finally {
    loading.value = false
  }
}

async function syncClusters() {
  syncing.value = true
  try {
    clusters.value = await api.syncClusters(props.id)
    const count = clusters.value.length
    notify(`Synced ${count} cluster${count === 1 ? '' : 's'} from ${PROVIDERS[account.value.provider]}`, 'success')
  } catch (error) {
    notify(`Cluster sync failed: ${error.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

async function deleteAccount() {
  const { name } = account.value
  const message =
    `Delete account "${name}"?\n\n` +
    'Its stored clusters and resources are removed from CloudHop. Nothing changes in the cloud.'
  if (!window.confirm(message)) return
  deleting.value = true
  try {
    await accounts.remove(props.id)
    notify(`Account "${name}" deleted`, 'success')
    router.push({ name: 'home' })
  } catch (error) {
    notify(`Could not delete account: ${error.message}`, 'error')
    deleting.value = false
  }
}

function machineTypes(cluster) {
  const types = new Set((cluster.spec.node_pools || []).map((pool) => pool.machine_type).filter(Boolean))
  return [...types].join(', ')
}
</script>

<template>
  <div class="page">
    <div v-if="!account && accounts.loading" class="empty"><span class="spinner" /></div>

    <div v-else-if="!account" class="card empty">
      <h3>Account not found</h3>
      <p>It may have been deleted.</p>
      <RouterLink to="/">Back to start</RouterLink>
    </div>

    <template v-else>
      <header class="page-header">
        <div>
          <div class="breadcrumb provider-line">
            <ProviderLogo :provider="account.provider" :size="16" />
            {{ PROVIDERS[account.provider] }} account
          </div>
          <h1>{{ account.name }}</h1>
          <div class="meta">
            <span v-if="account.project_id">Project <code>{{ account.project_id }}</code></span>
            <span>ID <code>{{ account.external_id }}</code></span>
            <span v-if="!account.is_active" class="badge warning">inactive</span>
            <span v-else-if="account.is_valid" class="badge success">credential valid</span>
            <span v-else class="badge warning">not validated</span>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-danger" :disabled="deleting || syncing" @click="deleteAccount">
            <AppIcon name="trash" :size="14" />
            Delete
          </button>
          <button class="btn btn-primary" :disabled="syncing || !account.is_valid" @click="syncClusters">
            <span v-if="syncing" class="spinner" />
            <AppIcon v-else name="refresh" :size="14" />
            {{ syncing ? 'Syncing clusters…' : 'Sync clusters' }}
          </button>
        </div>
      </header>

      <section class="card">
        <div class="section-header">
          <h2>Clusters</h2>
          <span v-if="lastSynced" class="muted" :title="dateTime(lastSynced)">
            Last synced {{ timeAgo(lastSynced) }}
          </span>
        </div>

        <div v-if="loading" class="empty"><span class="spinner" /></div>
        <div v-else-if="loadError" class="empty">
          <div class="alert danger">{{ loadError }}</div>
        </div>
        <div v-else-if="!clusters.length" class="empty">
          <AppIcon name="server" :size="32" />
          <h3>No clusters yet</h3>
          <p>Use <strong>Sync clusters</strong> to fetch this account's Kubernetes clusters.</p>
        </div>

        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Location</th>
                <th>Version</th>
                <th>Nodes</th>
                <th>Machine type</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="cluster in clusters"
                :key="cluster.id"
                class="clickable"
                @click="router.push({ name: 'cluster', params: { id: cluster.id } })"
              >
                <td>
                  <RouterLink :to="{ name: 'cluster', params: { id: cluster.id } }" class="cluster-name" @click.stop>
                    {{ cluster.name }}
                  </RouterLink>
                  <span v-if="cluster.spec.autopilot" class="badge plain info">autopilot</span>
                </td>
                <td><StatusBadge :status="cluster.status" /></td>
                <td>{{ cluster.location }}</td>
                <td class="mono">{{ cluster.kubernetes_version }}</td>
                <td>{{ cluster.spec.node_count ?? '–' }}</td>
                <td class="mono">{{ machineTypes(cluster) || '–' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <BillingCard :account="account" />
    </template>
  </div>
</template>

<style scoped>
.provider-line {
  display: flex;
  gap: 6px;
  align-items: center;
}

.cluster-name {
  margin-right: 8px;
  font-weight: 600;
}

.empty > svg {
  color: var(--text-muted);
}
</style>
