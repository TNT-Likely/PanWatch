import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@aiwatch/api': path.resolve(__dirname, './packages/api/src'),
      '@aiwatch/base-ui': path.resolve(__dirname, './packages/base-ui/src'),
      '@aiwatch/biz-ui': path.resolve(__dirname, './packages/biz-ui/src'),
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
