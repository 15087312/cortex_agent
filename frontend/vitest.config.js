import { defineConfig } from 'vitest/config'
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
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.spec.js'],
    setupFiles: ['src/test/setup.js'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{vue,js}'],
      exclude: [
        'src/main.js',
        'src/test/**',
        'src/**/*.spec.js',
        'src/assets/**',
      ],
      reporter: ['text', 'html'],
    },
  },
})
