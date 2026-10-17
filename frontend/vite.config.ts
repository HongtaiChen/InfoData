import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'
import { fileURLToPath, URL } from 'node:url'

// ─────────────────────────────────────────────────────────────
// 端口 / 后端地址可配（2026-10-17 改）
//
// 原来写死 5173 与 127.0.0.1:8000，本机端口一旦冲突只能改源码，
// 而且改完还得记得两处一起改。现改为：
//
//   取值优先级：进程环境变量 > frontend/.env.local（已 gitignore）> 默认值
//
//   INFO_DATA_WEB_PORT    前端 dev server 端口        默认 5173
//   INFO_DATA_WEB_HOST    监听地址                    默认 127.0.0.1
//   INFO_DATA_API_PORT    后端端口                    默认 8000（用来拼默认代理地址）
//   INFO_DATA_API_TARGET  完整后端地址                默认 http://127.0.0.1:<API_PORT>
//                         指向远程 / 容器后端时设这个，它优先于 API_PORT
//
// start-dev.bat 读的是同一组变量名，两边不会各自漂移。
// ─────────────────────────────────────────────────────────────

export default defineConfig(({ mode }) => {
  // 第三参传 '' 表示不限制变量前缀 —— 否则只能读到 VITE_ 开头的键，
  // 读不到项目统一使用的 INFO_DATA_*。
  const fileEnv = loadEnv(mode, process.cwd(), '')
  const pick = (key: string, fallback: string): string =>
    process.env[key] || fileEnv[key] || fallback

  const webPort = Number(pick('INFO_DATA_WEB_PORT', '5173'))
  const webHost = pick('INFO_DATA_WEB_HOST', '127.0.0.1')
  const apiPort = pick('INFO_DATA_API_PORT', '8000')
  const apiTarget = pick('INFO_DATA_API_TARGET', `http://127.0.0.1:${apiPort}`)

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: webHost,
      port: webPort,
      // 端口被占时直接失败，不要静默挪到下一个端口 ——
      // 否则 start-dev.bat 打印的地址与实际监听不一致，改了配置也看不出没生效。
      strictPort: true,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
