import axios from 'axios'
import { createDiscreteApi } from 'naive-ui'

const { message } = createDiscreteApi(['message'])

// 同一 URL 错误提示节流（5 秒内只弹一次，避免轮询等高频请求刷屏）
const toastMap = new Map<string, number>()
const TOAST_GAP = 5000

function toastError(url: string, text: string) {
  const now = Date.now()
  const last = toastMap.get(url) || 0
  if (now - last < TOAST_GAP) return
  toastMap.set(url, now)
  message.error(text, { duration: 3500 })
}

function extractErrorText(err: any, url?: string): string {
  if (err?.response?.data?.detail) {
    const d = err.response.data.detail
    return typeof d === 'string' ? d : JSON.stringify(d).slice(0, 120)
  }
  if (err?.code === 'ECONNABORTED') return '请求超时，请稍后重试'
  if (!err?.response) return `网络错误：${err?.message || '无法连接服务器'}`
  const status = err.response.status
  const map: Record<number, string> = {
    400: '请求参数错误',
    401: '未授权，请检查登录状态',
    403: '没有权限执行此操作',
    404: '请求的资源不存在',
    429: '请求过于频繁，请稍后再试',
    500: '服务器内部错误',
    502: '网关错误，后端服务异常',
    503: '服务暂不可用',
  }
  return map[status] || `请求失败（HTTP ${status}）`
}

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

api.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const url = err?.config?.url || 'unknown'
    // 视图内已自行处理错误展示的请求，通过 silent 配置跳过全局提示
    const silent = (err?.config as any)?.silent === true
    console.error('[API]', url, err.message)
    if (!silent) {
      toastError(url, extractErrorText(err, url))
    }
    return Promise.reject(err)
  },
)

export default api
