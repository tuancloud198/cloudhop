<script setup>
import AppIcon from '../components/AppIcon.vue'
import { accounts } from '../accounts.js'

defineEmits(['add-account'])
</script>

<template>
  <div class="page">
    <div class="card empty welcome">
      <AppIcon name="cloud" :size="40" />
      <template v-if="accounts.items.length">
        <h3>Pick an account</h3>
        <p>Choose an account on the left to see and sync its Kubernetes clusters.</p>
      </template>
      <template v-else>
        <h3>Connect your first cloud account</h3>
        <p>Upload a service account key. CloudHop then lists the account's clusters and their resources.</p>
        <button class="btn btn-primary" @click="$emit('add-account')">
          <AppIcon name="plus" :size="14" />
          Add account
        </button>
        <p class="hint">
          First time? The <RouterLink :to="{ name: 'guide' }">guide</RouterLink> explains how to prepare a service
          account.
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 64px 24px;
}

.welcome > svg {
  color: var(--primary);
}

.welcome p {
  max-width: 420px;
  margin: 0 0 12px;
}

.welcome .hint {
  margin: 8px 0 0;
  font-size: 13px;
}
</style>
