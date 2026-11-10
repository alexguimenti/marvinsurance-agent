/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  build: {
    // the Supabase client is most of the bundle: a chunk of its own keeps the build free of the size warning
    rolldownOptions: { output: { codeSplitting: { groups: [{ name: 'supabase', test: /node_modules[\/]@supabase/ }] } } },
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://127.0.0.1:8000',
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    // the tests never use the real project: these stand in for VITE_SUPABASE_URL and the publishable key
    env: { VITE_SUPABASE_URL: 'http://127.0.0.1:54321', VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test' },
  },
})
