import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/admin': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/webhooks': 'http://localhost:8000',
      '/backfill': 'http://localhost:8000',
      '/baixas': 'http://localhost:8000',
      '/queue': 'http://localhost:8000',
      '/expenses': 'http://localhost:8000',
      '/dashboard': 'http://localhost:8000',
    },
  },
})
