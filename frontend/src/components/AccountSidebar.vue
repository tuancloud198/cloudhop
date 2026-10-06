<script setup>
import AppIcon from './AppIcon.vue'
import ProviderLogo from './ProviderLogo.vue'
import ThemeSwitch from './ThemeSwitch.vue'
import { accounts } from '../accounts.js'
import { PROVIDERS } from '../format.js'

defineEmits(['add', 'navigate'])
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <AppIcon name="cloud" :size="22" />
      <span>CloudHop</span>
    </div>

    <div class="heading">
      <span>Accounts</span>
      <button class="btn btn-sm" @click="$emit('add')">
        <AppIcon name="plus" :size="14" />
        Add
      </button>
    </div>

    <nav class="list">
      <div v-if="accounts.loading && !accounts.items.length" class="note">
        <span class="spinner" /> Loading…
      </div>
      <div v-else-if="accounts.error" class="note error">
        {{ accounts.error }}
        <button class="btn btn-sm" @click="accounts.load()">Retry</button>
      </div>
      <div v-else-if="!accounts.items.length" class="note">No accounts yet.</div>

      <RouterLink
        v-for="account in accounts.items"
        :key="account.id"
        :to="{ name: 'account', params: { id: account.id } }"
        class="item"
        @click="$emit('navigate')"
      >
        <span class="provider" :title="PROVIDERS[account.provider]">
          <ProviderLogo :provider="account.provider" :size="22" />
        </span>
        <span class="text">
          <span class="name">{{ account.name }}</span>
          <span class="sub mono">{{ account.project_id || PROVIDERS[account.provider] }}</span>
        </span>
        <span
          class="dot"
          :class="{ invalid: !account.is_valid || !account.is_active }"
          :title="account.is_valid && account.is_active ? 'Credential valid' : 'Inactive or not validated'"
        />
      </RouterLink>
    </nav>

    <footer class="footer">
      <ThemeSwitch />
    </footer>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: var(--sidebar-width);
  height: 100%;
  border-right: 1px solid var(--border);
  background: var(--surface);
}

.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 18px 20px;
  color: var(--primary);
  font-size: 17px;
  font-weight: 700;
}

.brand span {
  color: var(--text);
}

.heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px 8px 20px;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  padding: 4px 10px 16px;
  overflow-y: auto;
}

.footer {
  padding: 12px;
  border-top: 1px solid var(--border);
}

.note {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
  padding: 8px 10px;
  color: var(--text-muted);
}

.note.error {
  color: var(--danger);
}

.item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 6px;
  color: var(--text);
}

.item:hover {
  background: var(--surface-muted);
  text-decoration: none;
}

.item.router-link-active {
  background: var(--primary-soft);
}

.provider {
  display: grid;
  flex-shrink: 0;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
}

.text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.name,
.sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name {
  font-weight: 500;
}

.sub {
  color: var(--text-muted);
  font-size: 11.5px;
}

.dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
}

.dot.invalid {
  background: var(--warning);
}
</style>
