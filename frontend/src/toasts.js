import { reactive } from 'vue'

export const toasts = reactive([])

let nextId = 1

// type: success | error | info
export function notify(message, type = 'info', timeout = type === 'error' ? 8000 : 4000) {
  const id = nextId++
  toasts.push({ id, message, type })
  setTimeout(() => dismiss(id), timeout)
}

export function dismiss(id) {
  const index = toasts.findIndex((toast) => toast.id === id)
  if (index !== -1) toasts.splice(index, 1)
}
