/**
 * 前端调用后端的唯一地址口径。
 *
 * 同域部署时保持 `/api`；静态展示站通过 VITE_API_BASE_URL 指向 Render Web Service。
 * 路径统一从这里拼，避免 axios、SSE 和截图链接各自维护一份域名。
 */
const configured = import.meta.env?.VITE_API_BASE_URL?.trim()

export const API_BASE_URL = (configured || '/api').replace(/\/$/, '')

export function apiUrl(path = '') {
  if (/^https?:\/\//i.test(path)) return path
  // dev-only 的 Vite mock 必须继续请求当前开发服务器，不能拼到远端 API。
  if (path.startsWith('/__mock__/')) return path

  let normalized = path.startsWith('/') ? path : `/${path}`
  // 调用方历史上使用 `/api/chat`；base 已经带 `/api` 时去掉重复前缀。
  if (normalized === '/api') normalized = ''
  else if (normalized.startsWith('/api/')) normalized = normalized.slice(4)
  return `${API_BASE_URL}${normalized}`
}
