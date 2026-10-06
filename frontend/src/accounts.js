import { reactive } from 'vue'

import { api } from './api.js'

// Accounts shared by the sidebar and the account page
export const accounts = reactive({
  items: [],
  loading: false,
  error: null,

  async load() {
    this.loading = true
    this.error = null
    try {
      this.items = await api.listAccounts()
    } catch (error) {
      this.error = error.message
    } finally {
      this.loading = false
    }
  },

  find(id) {
    return this.items.find((account) => account.id === Number(id))
  },

  add(account) {
    this.items = [...this.items, account].sort((a, b) => a.name.localeCompare(b.name))
  },

  async remove(id) {
    await api.deleteAccount(id)
    this.items = this.items.filter((account) => account.id !== Number(id))
  },
})
