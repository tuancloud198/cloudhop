<script setup>
import { computed } from 'vue'

// GKE cluster statuses; others fall back to a neutral badge
const TONES = {
  RUNNING: 'success',
  PROVISIONING: 'info',
  RECONCILING: 'info',
  STOPPING: 'warning',
  DEGRADED: 'warning',
  ERROR: 'danger',
}

const props = defineProps({ status: { type: String, default: '' } })
const tone = computed(() => TONES[props.status] || '')
const label = computed(() => (props.status || 'unknown').toLowerCase())
</script>

<template>
  <span class="badge" :class="tone">{{ label }}</span>
</template>
