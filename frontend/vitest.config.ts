import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@panwatch/api': path.resolve(__dirname, './packages/api/src'),
      '@panwatch/base-ui': path.resolve(__dirname, './packages/base-ui/src'),
      '@panwatch/biz-ui': path.resolve(__dirname, './packages/biz-ui/src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
