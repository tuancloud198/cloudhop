<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { stringify } from 'yaml'

import AppIcon from './AppIcon.vue'
import { apiVersion, dateTime } from '../format.js'

const props = defineProps({ resource: { type: Object, default: null } })
const emit = defineEmits(['close'])

const tab = ref('manifest')
const copied = ref(false)
const revealed = ref(false)

const isSecret = computed(
  () => props.resource && !props.resource.group && props.resource.kind === 'Secret',
)
const canReveal = computed(
  () => isSecret.value && tab.value === 'manifest' && Object.keys(props.resource.manifest?.data || {}).length > 0,
)

const text = computed(() => {
  if (!props.resource) return ''
  let value = tab.value === 'manifest' ? props.resource.manifest : props.resource.status
  if (canReveal.value && revealed.value) value = decodeSecret(value)
  return value && Object.keys(value).length ? stringify(value) : ''
})

watch(
  () => props.resource?.id,
  () => {
    tab.value = 'manifest'
    copied.value = false
    revealed.value = false
  },
)

// Move base64 `data` values into `stringData` as plain text, so the YAML stays applicable.
// Values that are not valid UTF-8 (binary keys, certificates in DER, ...) stay in `data`.
function decodeSecret(manifest) {
  const data = {}
  const stringData = { ...manifest.stringData }
  for (const [key, encoded] of Object.entries(manifest.data)) {
    const decoded = decodeBase64(encoded)
    if (decoded === null) data[key] = encoded
    else stringData[key] = decoded
  }
  const result = {}
  for (const [key, value] of Object.entries(manifest)) {
    if (key === 'stringData') continue
    if (key !== 'data') {
      result[key] = value
      continue
    }
    if (Object.keys(data).length) result.data = data
    result.stringData = stringData
  }
  return result
}

function decodeBase64(encoded) {
  try {
    const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0))
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    return null
  }
}

async function copy() {
  await navigator.clipboard.writeText(text.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

function onKeydown(event) {
  if (event.key === 'Escape' && props.resource) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Transition name="drawer">
    <div v-if="resource" class="overlay" @click.self="emit('close')">
      <aside class="drawer" role="dialog" :aria-label="`${resource.kind} ${resource.name}`">
        <header>
          <div class="title">
            <span class="badge plain info">{{ resource.kind }}</span>
            <h2 class="mono">{{ resource.name }}</h2>
            <div class="meta">
              <span v-if="resource.namespace">Namespace <code>{{ resource.namespace }}</code></span>
              <span v-else>Cluster-scoped</span>
              <span><code>{{ apiVersion(resource) }}</code></span>
              <span v-if="resource.kube_created_at">Created {{ dateTime(resource.kube_created_at) }}</span>
            </div>
          </div>
          <button class="btn btn-ghost btn-icon" aria-label="Close" @click="emit('close')">
            <AppIcon name="x" />
          </button>
        </header>

        <div class="tabs">
          <button :class="{ active: tab === 'manifest' }" @click="tab = 'manifest'">Manifest</button>
          <button :class="{ active: tab === 'status' }" @click="tab = 'status'">Status</button>
          <span class="spacer" />
          <button v-if="canReveal" class="btn btn-sm" :aria-pressed="revealed" @click="revealed = !revealed">
            <AppIcon :name="revealed ? 'eye-off' : 'eye'" :size="13" />
            {{ revealed ? 'Hide values' : 'Decode values' }}
          </button>
          <button class="btn btn-sm" :disabled="!text" @click="copy">
            <AppIcon :name="copied ? 'check' : 'copy'" :size="13" />
            {{ copied ? 'Copied' : 'Copy YAML' }}
          </button>
        </div>

        <pre v-if="text" class="code">{{ text }}</pre>
        <div v-else class="empty">This object has no {{ tab }}.</div>
      </aside>
    </div>
  </Transition>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  justify-content: flex-end;
  background: rgb(10 14 20 / 35%);
}

.drawer {
  display: flex;
  flex-direction: column;
  width: min(720px, 100vw);
  height: 100%;
  border-left: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-lg);
}

header {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  padding: 18px 20px 12px;
}

.title {
  min-width: 0;
}

.title h2 {
  margin-top: 6px;
  font-size: 16px;
  word-break: break-all;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 13px;
}

.tabs {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
}

.tabs > button:not(.btn) {
  padding: 10px 12px;
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--text-muted);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
}

.tabs > button.active {
  border-bottom-color: var(--primary);
  color: var(--text);
}

.spacer {
  flex: 1;
}

.code {
  flex: 1;
  margin: 0;
  padding: 16px 20px;
  overflow: auto;
  background: var(--code-bg);
  line-height: 1.55;
  tab-size: 2;
}

.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.2s ease;
}

.drawer-enter-active .drawer,
.drawer-leave-active .drawer {
  transition: transform 0.2s ease;
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-from .drawer,
.drawer-leave-to .drawer {
  transform: translateX(40px);
}
</style>
