<script setup>
import AppIcon from './AppIcon.vue'
import { dismiss, toasts } from '../toasts.js'
</script>

<template>
  <div class="toasts" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type">
        <span class="message">{{ toast.message }}</span>
        <button class="btn btn-ghost btn-icon btn-sm" aria-label="Dismiss" @click="dismiss(toast.id)">
          <AppIcon name="x" :size="14" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(420px, calc(100vw - 40px));
}

.toast {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 10px 8px 10px 14px;
  border: 1px solid var(--border);
  border-left: 4px solid var(--primary);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-lg);
}

.toast.success {
  border-left-color: var(--success);
}

.toast.error {
  border-left-color: var(--danger);
}

.message {
  flex: 1;
  padding-top: 3px;
  white-space: pre-line;
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.2s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
