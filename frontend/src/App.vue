<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AccountSidebar from './components/AccountSidebar.vue'
import AddAccountDialog from './components/AddAccountDialog.vue'
import AppIcon from './components/AppIcon.vue'
import ToastStack from './components/ToastStack.vue'
import { accounts } from './accounts.js'
import { notify } from './toasts.js'

const router = useRouter()
const adding = ref(false)
// Sidebar is a drawer on narrow screens
const sidebarOpen = ref(false)

onMounted(() => accounts.load())

function onCreated(account) {
  accounts.add(account)
  notify(`Account "${account.name}" added`, 'success')
  router.push({ name: 'account', params: { id: account.id } })
}

function openAdd() {
  sidebarOpen.value = false
  adding.value = true
}
</script>

<template>
  <div class="shell" :class="{ 'sidebar-open': sidebarOpen }">
    <AccountSidebar class="side" @add="openAdd" @navigate="sidebarOpen = false" />
    <div class="scrim" @click="sidebarOpen = false" />

    <main class="main">
      <div class="topbar">
        <button class="btn btn-ghost btn-icon" aria-label="Show accounts" @click="sidebarOpen = true">
          <AppIcon name="menu" />
        </button>
        <span class="brand">CloudHop</span>
      </div>
      <RouterView @add-account="openAdd" />
    </main>
  </div>

  <AddAccountDialog v-model:open="adding" @created="onCreated" />
  <ToastStack />
</template>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
}

.side {
  flex-shrink: 0;
}

.main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}

.topbar,
.scrim {
  display: none;
}

@media (max-width: 860px) {
  .side {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 50;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }

  .sidebar-open .side {
    transform: none;
  }

  .sidebar-open .scrim {
    position: fixed;
    inset: 0;
    z-index: 40;
    display: block;
    background: rgb(10 14 20 / 40%);
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  .topbar .brand {
    font-weight: 700;
  }
}
</style>
