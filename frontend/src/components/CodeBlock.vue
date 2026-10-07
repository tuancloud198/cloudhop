<script setup>
import { ref } from 'vue'

import AppIcon from './AppIcon.vue'

const props = defineProps({
  code: { type: String, required: true },
  // Shown above the code, e.g. a file name
  label: { type: String, default: '' },
})

const copied = ref(false)

async function copy() {
  await navigator.clipboard.writeText(props.code)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
</script>

<template>
  <div class="code-block">
    <div class="bar">
      <span class="label mono">{{ label }}</span>
      <button class="btn btn-ghost btn-sm" :aria-label="`Copy ${label || 'code'}`" @click="copy">
        <AppIcon :name="copied ? 'check' : 'copy'" :size="13" />
        {{ copied ? 'Copied' : 'Copy' }}
      </button>
    </div>
    <pre>{{ code }}</pre>
  </div>
</template>

<style scoped>
.code-block {
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--code-bg);
}

.bar {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--border);
}

.label {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

pre {
  margin: 0;
  padding: 12px 14px;
  overflow-x: auto;
  line-height: 1.55;
  tab-size: 2;
}
</style>
