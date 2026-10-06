import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Django serves the API; override with CLOUDHOP_API=http://host:port npm run dev
const apiTarget = process.env.CLOUDHOP_API || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})
