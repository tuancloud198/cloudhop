<script setup>
import AppIcon from './AppIcon.vue'
import { theme } from '../theme.js'

const OPTIONS = [
  { value: 'system', label: 'System', icon: 'monitor' },
  { value: 'light', label: 'Light', icon: 'sun' },
  { value: 'dark', label: 'Dark', icon: 'moon' },
]
</script>

<template>
  <div class="switch" role="radiogroup" aria-label="Theme">
    <button
      v-for="option in OPTIONS"
      :key="option.value"
      type="button"
      role="radio"
      :aria-checked="theme === option.value"
      :class="{ active: theme === option.value }"
      :title="`${option.label} theme`"
      @click="theme = option.value"
    >
      <AppIcon :name="option.icon" :size="14" />
      <span>{{ option.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.switch {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-muted);
}

button {
  display: flex;
  gap: 5px;
  align-items: center;
  justify-content: center;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
}

button:hover {
  color: var(--text);
}

button.active {
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow);
}

button:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 1px;
}
</style>
