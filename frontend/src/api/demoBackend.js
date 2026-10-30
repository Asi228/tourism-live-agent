import { apiUrl } from './url.js'

let wakingPromise = null

/**
 * 唤醒免费演示后端。多个入口同时调用时复用同一个请求，避免重复探测。
 * Render 冷启动通常需要几十秒，因此超时留到 90 秒。
 */
export function wakeDemoBackend(timeoutMs = 90000) {
  if (wakingPromise) return wakingPromise

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  wakingPromise = fetch(apiUrl('/health'), {
    method: 'GET',
    cache: 'no-store',
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const body = await resp.json()
      if (body?.code !== 0 || body?.data?.api !== 'ok') {
        throw new Error(body?.message || '演示服务尚未就绪')
      }
      return body.data
    })
    .finally(() => {
      clearTimeout(timer)
      wakingPromise = null
    })
  return wakingPromise
}
