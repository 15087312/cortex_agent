import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { readFileSync } from 'fs'

const pkg = JSON.parse(readFileSync(resolve(__dirname, 'package.json'), 'utf-8'))

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
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
