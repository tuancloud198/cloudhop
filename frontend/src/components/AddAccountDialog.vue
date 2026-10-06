<script setup>
import { computed, ref, watch } from 'vue'

import AppIcon from './AppIcon.vue'
import ProviderLogo from './ProviderLogo.vue'
import { api } from '../api.js'

const open = defineModel('open', { type: Boolean, default: false })
const emit = defineEmits(['created'])

// Only providers with a credential adapter on the backend can be chosen
const PROVIDERS = [
  { value: 'gcp', label: 'Google Cloud', enabled: true, hint: 'Service account key (.json)' },
  { value: 'aws', label: 'AWS', enabled: false },
  { value: 'azure', label: 'Azure', enabled: false },
]

const dialog = ref(null)
const fileInput = ref(null)
const name = ref('')
const provider = ref('gcp')
const file = ref(null)
// What the key file says, shown so the user can check they picked the right one
const preview = ref(null)
const dragging = ref(false)
const submitting = ref(false)
const errors = ref({})

const canSubmit = computed(() => name.value.trim() && file.value && !preview.value?.error && !submitting.value)

watch(open, (value) => {
  if (value) {
    reset()
    dialog.value?.showModal()
  } else {
    dialog.value?.close()
  }
})

function reset() {
  name.value = ''
  provider.value = 'gcp'
  file.value = null
  preview.value = null
  errors.value = {}
  if (fileInput.value) fileInput.value.value = ''
}

function close() {
  if (!submitting.value) open.value = false
}

async function pick(selected) {
  if (!selected) return
  file.value = selected
  errors.value = {}
  try {
    const key = JSON.parse(await selected.text())
    if (key.type !== 'service_account') {
      preview.value = { error: 'This is not a GCP service account key (its "type" is not "service_account").' }
      return
    }
    preview.value = { projectId: key.project_id, email: key.client_email }
    if (!name.value.trim() && key.project_id) name.value = key.project_id
  } catch {
    preview.value = { error: 'This file is not valid JSON.' }
  }
}

function onDrop(event) {
  dragging.value = false
  pick(event.dataTransfer.files[0])
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  errors.value = {}
  const form = new FormData()
  form.append('name', name.value.trim())
  form.append('provider', provider.value)
  form.append('credential_file', file.value)
  try {
    const account = await api.createAccount(form)
    emit('created', account)
    submitting.value = false
    open.value = false
  } catch (error) {
    errors.value = Object.keys(error.fields).length ? error.fields : { detail: error.message }
    submitting.value = false
  }
}

function fieldError(field) {
  return [].concat(errors.value[field] || []).join('\n')
}
</script>

<template>
  <dialog ref="dialog" class="dialog" @cancel.prevent="close" @click.self="close">
    <form class="body" @submit.prevent="submit">
      <header>
        <div>
          <h2>Add cloud account</h2>
          <p class="muted">Upload a credential; CloudHop checks it against the provider before saving.</p>
        </div>
        <button type="button" class="btn btn-ghost btn-icon" aria-label="Close" @click="close">
          <AppIcon name="x" />
        </button>
      </header>

      <div class="field">
        <label>Provider</label>
        <div class="providers">
          <label
            v-for="option in PROVIDERS"
            :key="option.value"
            class="provider"
            :class="{ selected: provider === option.value, disabled: !option.enabled }"
          >
            <input v-model="provider" type="radio" :value="option.value" :disabled="!option.enabled" />
            <ProviderLogo :provider="option.value" :size="28" />
            <span>{{ option.label }}</span>
            <small v-if="!option.enabled" class="muted">coming soon</small>
          </label>
        </div>
      </div>

      <div class="field">
        <label>Credential file</label>
        <div
          class="dropzone"
          :class="{ dragging, filled: file && !preview?.error, rejected: preview?.error }"
          role="button"
          tabindex="0"
          @click="fileInput.click()"
          @keydown.enter.prevent="fileInput.click()"
          @dragover.prevent="dragging = true"
          @dragleave="dragging = false"
          @drop.prevent="onDrop"
        >
          <AppIcon :name="!file ? 'upload' : preview?.error ? 'alert' : 'check'" :size="22" />
          <template v-if="file">
            <strong>{{ file.name }}</strong>
            <span class="muted">Click or drop to replace</span>
          </template>
          <template v-else>
            <strong>Drop a service account key here</strong>
            <span class="muted">or click to choose a .json file</span>
          </template>
        </div>
        <input
          ref="fileInput"
          type="file"
          accept=".json,application/json"
          hidden
          @change="pick($event.target.files[0])"
        />
        <div v-if="preview?.error" class="field-error">{{ preview.error }}</div>
        <dl v-else-if="preview" class="preview">
          <dt>Project</dt>
          <dd class="mono">{{ preview.projectId }}</dd>
          <dt>Service account</dt>
          <dd class="mono">{{ preview.email }}</dd>
        </dl>
        <div v-if="fieldError('credential_file')" class="field-error">{{ fieldError('credential_file') }}</div>
      </div>

      <div class="field">
        <label for="account-name">Name</label>
        <input id="account-name" v-model="name" class="input" maxlength="100" placeholder="e.g. production" />
        <div v-if="fieldError('name')" class="field-error">{{ fieldError('name') }}</div>
      </div>

      <div v-if="errors.detail" class="alert danger">{{ errors.detail }}</div>

      <footer>
        <button type="button" class="btn" :disabled="submitting" @click="close">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="!canSubmit">
          <span v-if="submitting" class="spinner" />
          {{ submitting ? 'Validating…' : 'Add account' }}
        </button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.dialog {
  width: min(520px, calc(100vw - 32px));
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow-lg);
}

.dialog::backdrop {
  background: rgb(10 14 20 / 45%);
}

.body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px 24px;
}

header {
  display: flex;
  gap: 12px;
  justify-content: space-between;
}

header p {
  margin: 4px 0 0;
}

.providers {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.provider {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 12px 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-weight: 500;
  cursor: pointer;
}

.provider input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.provider.selected {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.provider.disabled {
  filter: grayscale(1);
  opacity: 0.55;
  cursor: not-allowed;
}

.provider small {
  font-size: 11px;
  font-weight: 400;
}

.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 22px 16px;
  border: 1.5px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-muted);
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.dropzone strong {
  color: var(--text);
  font-weight: 500;
  word-break: break-all;
}

.dropzone:hover,
.dropzone.dragging {
  border-color: var(--primary);
  background: var(--primary-soft);
}

.dropzone.filled {
  border-style: solid;
  color: var(--success);
}

.dropzone.rejected {
  border-style: solid;
  border-color: var(--danger);
  color: var(--danger);
}

.preview {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 12px;
  margin: 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--surface-muted);
}

.preview dt {
  color: var(--text-muted);
}

.preview dd {
  margin: 0;
  word-break: break-all;
}

footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
</style>
