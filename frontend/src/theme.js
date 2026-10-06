import { ref, watch } from 'vue'

// Also read by the inline script in index.html, which applies the theme before the app loads
const STORAGE_KEY = 'cloudhop-theme'
export const THEMES = ['system', 'light', 'dark']

function stored() {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return THEMES.includes(value) ? value : 'system'
  } catch {
    // Storage can be blocked (private mode, disabled site data)
    return 'system'
  }
}

export const theme = ref(stored())

watch(theme, (value) => {
  if (value === 'system') {
    delete document.documentElement.dataset.theme
  } else {
    document.documentElement.dataset.theme = value
  }
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // The choice then lasts until the page is reloaded
  }
})
