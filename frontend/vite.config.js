import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { readFileSync } from 'fs'

// 统一版本读取路径：以根目录 VERSION 文件为单一来源（与后端 cortex/version.py、pyproject.toml 同源）
const __appVersion = readFileSync(resolve(__dirname, '../VERSION'), 'utf-8').trim()

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(__appVersion),
  },
  server: {
    port: 5173,
    proxy: {
      // 统一入口：全部指向主架构后端 :8080（api.main:app，含 chat_gateway /stream/*）
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      // /stream 保留前缀：chat_gateway 的 WS 端点是 /stream/ws/{sid}
      '/stream': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
