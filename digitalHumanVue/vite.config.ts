import { fileURLToPath, URL } from 'node:url'
import { existsSync, readFileSync } from 'node:fs'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  // 可选 HTTPS：SSL_CERT / SSL_KEY（或 VITE_SSL_CERT / VITE_SSL_KEY）均非空且文件存在时启用，
  // 否则保持纯 HTTP（默认行为不变）。优先非 VITE_ 命名，避免证书路径暴露给客户端。
  const sslCertPath = env.SSL_CERT || env.VITE_SSL_CERT || ''
  const sslKeyPath = env.SSL_KEY || env.VITE_SSL_KEY || ''
  const sslEnabled = Boolean(
    sslCertPath && sslKeyPath && existsSync(sslCertPath) && existsSync(sslKeyPath),
  )

  return {
    plugins: [
      vue(),
      vueDevTools(),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      ...(sslEnabled
        ? {
            https: {
              cert: readFileSync(sslCertPath),
              key: readFileSync(sslKeyPath),
            },
          }
        : {}),
      proxy: {
        '/api': {
          target: env.VITE_API_BASE || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
