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
      // 本机 8000 被无关服务占用，后端实际跑 8001；需要其他端口时用 BACKEND_PORT 覆盖
      '/api': `http://127.0.0.1:${process.env.BACKEND_PORT || 8001}`,
    },
  },
})
