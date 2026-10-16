import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy: the dashboard calls /api/* and Vite forwards to Django,
// so no CORS configuration is needed during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
