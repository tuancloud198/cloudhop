<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import AppIcon from '../components/AppIcon.vue'
import { api } from '../api.js'
import { FINISHED, STEP_LABELS, statusTone } from '../moves.js'
import { dateTime, timeAgo } from '../format.js'
import { notify } from '../toasts.js'

// Seconds between refreshes while the move runs
const POLL_SECONDS = 4

const props = defineProps({ id: { type: String, required: true } })

const move = ref(null)
const loadError = ref(null)
const acting = ref(false)
let timer

const running = computed(() => move.value && !FINISHED.includes(move.value.status))
// Where the move is: the current step, or the one it failed or was cancelled at
const reached = computed(() => {
  const value = move.value
  if (!value) return -1
  if (value.status === 'done') return value.steps.length
  if (value.status === 'failed') return value.steps.indexOf(value.failed_step)
  if (value.status === 'cancelled') {
    const last = [...value.events].reverse().find((event) => value.steps.includes(event.step))
    return last ? value.steps.indexOf(last.step) : 0
  }
  return Math.max(value.steps.indexOf(value.status), 0)
})
const replicas = computed(() => Object.entries(move.value?.replicas ?? {}))
const mapping = computed(() => Object.entries(move.value?.storage_class_mapping ?? {}))

watch(() => props.id, () => {
  move.value = null
  load()
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

async function load() {
  clearTimeout(timer)
  try {
    move.value = await api.getMove(props.id)
    loadError.value = null
  } catch (error) {
    loadError.value = error.message
  }
  if (!move.value || running.value) timer = setTimeout(load, POLL_SECONDS * 1000)
}

function stepState(index) {
  if (index < reached.value) return 'done'
  if (index > reached.value) return 'todo'
  if (move.value.status === 'failed') return 'failed'
  if (move.value.status === 'cancelled') return 'cancelled'
  return 'current'
}

async function act(action, label) {
  if (action === 'cancel' && !confirm('Cancel this move after its current step? Nothing it did is undone.')) return
  acting.value = true
  try {
    move.value = await (action === 'cancel' ? api.cancelMove(props.id) : api.retryMove(props.id))
    notify(label, 'success')
    load()
  } catch (error) {
    notify(error.message, 'error')
  } finally {
    acting.value = false
  }
}
</script>

<template>
  <div class="page">
    <div v-if="!move && !loadError" class="empty"><span class="spinner" /></div>
    <div v-else-if="!move" class="card empty">
      <h3>Move not found</h3>
      <p>{{ loadError }}</p>
      <RouterLink :to="{ name: 'moves' }">All moves</RouterLink>
    </div>

    <template v-else>
      <header class="page-header">
        <div>
          <div class="breadcrumb">
            <RouterLink :to="{ name: 'moves' }">Moves</RouterLink>
          </div>
          <h1>
            Move #{{ move.id }}
            <span class="badge" :class="statusTone(move.status)">{{ STEP_LABELS[move.status] }}</span>
          </h1>
          <div class="meta">
            <RouterLink :to="{ name: 'cluster', params: { id: move.source_cluster.id } }">
              {{ move.source_cluster.name }}
            </RouterLink>
            <span class="muted">({{ move.source_cluster.account_name }})</span>
            <AppIcon name="move" :size="14" />
            <RouterLink :to="{ name: 'cluster', params: { id: move.target_cluster.id } }">
              {{ move.target_cluster.name }}
            </RouterLink>
            <span class="muted">({{ move.target_cluster.account_name }})</span>
            <span>Namespaces <code>{{ move.namespaces.join(', ') }}</code></span>
            <span class="badge plain">{{ move.mode }}</span>
          </div>
        </div>
        <div class="actions">
          <button v-if="running" class="btn btn-danger" :disabled="acting" @click="act('cancel', 'Move cancelled')">
            Cancel
          </button>
          <button
            v-if="move.status === 'failed'"
            class="btn btn-primary"
            :disabled="acting"
            @click="act('retry', 'Retrying the move')"
          >
            <AppIcon name="refresh" :size="14" />
            Retry from {{ STEP_LABELS[move.failed_step]?.toLowerCase() }}
          </button>
        </div>
      </header>

      <section class="card">
        <div class="section-header">
          <h2>Steps</h2>
          <span class="muted small" :title="dateTime(move.created_at)">Started {{ timeAgo(move.created_at) }}</span>
        </div>
        <ol class="steps">
          <li v-for="(step, index) in move.steps" :key="step" :class="stepState(index)">
            <span class="marker">
              <span v-if="stepState(index) === 'current'" class="spinner" />
              <AppIcon v-else-if="stepState(index) === 'done'" name="check" :size="14" />
              <AppIcon v-else-if="stepState(index) === 'failed'" name="x" :size="14" />
              <span v-else>{{ index + 1 }}</span>
            </span>
            <div class="step-text">
              <strong>{{ STEP_LABELS[step] }}</strong>
              <span v-if="stepState(index) === 'current' && move.waiting_on" class="muted small">
                {{ move.waiting_on }}
              </span>
            </div>
          </li>
        </ol>
        <div v-if="move.error" class="body">
          <div class="alert danger">
            <AppIcon name="alert" />
            <div>{{ move.error }}</div>
          </div>
        </div>
        <div v-if="move.status === 'done' && move.mode === 'cutover'" class="body">
          <div class="alert success-note">
            <AppIcon name="check" />
            <div>
              The workloads run in the target. The source's are scaled to 0 and kept as they were, in case you need
              them back.
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="section-header"><h2>Details</h2></div>
        <dl class="facts">
          <dt>Velero backup</dt>
          <dd class="mono">{{ move.backup_name || '–' }}</dd>
          <dt>Velero restore</dt>
          <dd class="mono">{{ move.restore_name || '–' }}</dd>
          <dt>Storage location</dt>
          <dd class="mono">{{ move.storage_location }}</dd>
          <dt>Storage classes</dt>
          <dd>
            <span v-if="!mapping.length" class="muted">same names in the target</span>
            <span v-for="[from, to] in mapping" :key="from" class="mono pair">{{ from }} → {{ to }}</span>
          </dd>
          <template v-if="replicas.length">
            <dt>Replicas before the cutover</dt>
            <dd>
              <span v-for="[key, count] in replicas" :key="key" class="mono pair">{{ key }}: {{ count }}</span>
            </dd>
          </template>
        </dl>
      </section>

      <section class="card">
        <div class="section-header"><h2>Log</h2></div>
        <div class="table-wrap">
          <table class="table log">
            <thead>
              <tr>
                <th>Time</th>
                <th>Step</th>
                <th>What happened</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="event in [...move.events].reverse()" :key="event.id" :class="event.level">
                <td :title="dateTime(event.created_at)">{{ timeAgo(event.created_at) }}</td>
                <td>{{ STEP_LABELS[event.step] ?? event.step }}</td>
                <td class="message">{{ event.message }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
h1 {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
}

.small {
  font-size: 12.5px;
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 12px 20px 16px;
  list-style: none;
}

.steps li {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 6px 0;
}

.marker {
  display: grid;
  flex-shrink: 0;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 12px;
}

.step-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding-top: 3px;
}

.todo .step-text strong {
  color: var(--text-muted);
  font-weight: 500;
}

.done .marker {
  border-color: var(--success);
  background: var(--success-soft);
  color: var(--success);
}

.current .marker {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.failed .marker {
  border-color: var(--danger);
  background: var(--danger-soft);
  color: var(--danger);
}

.cancelled .marker {
  border-color: var(--warning);
  background: var(--warning-soft);
  color: var(--warning);
}

.body {
  padding: 0 20px 16px;
}

.success-note {
  background: var(--success-soft);
  color: var(--success);
}

.facts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 8px 20px;
  margin: 0;
  padding: 14px 20px 18px;
}

.facts dt {
  color: var(--text-muted);
}

.facts dd {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin: 0;
  min-width: 0;
}

.log .message {
  white-space: pre-line;
  min-width: 320px;
}

.log tr.warning td {
  color: var(--warning);
}

.log tr.error td {
  color: var(--danger);
}

@media (max-width: 640px) {
  .facts {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .facts dd {
    margin-bottom: 8px;
  }
}
</style>
