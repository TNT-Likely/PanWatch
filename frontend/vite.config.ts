import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  // Resolve bundled assets against the runtime <base>, including deep links.
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@panwatch/api': path.resolve(__dirname, './packages/api/src'),
      '@panwatch/base-ui': path.resolve(__dirname, './packages/base-ui/src'),
      '@panwatch/biz-ui': path.resolve(__dirname, './packages/biz-ui/src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5183,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
