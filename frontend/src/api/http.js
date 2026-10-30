import axios from 'axios'
import { ElMessage } from 'element-plus'

import { API_BASE_URL } from './url'

// 后端约定：HTTP 状态码恒为 200，**真实业务码在 body 里**（code / message / data）。
// 所以"请求成功"不等于"业务成功"—— 判断业务成败必须看 body.code。
const http = axios.create({
  baseURL: API_BASE_URL,
  // Agent 一次问答可能调 3 次大模型（判定 + 评估 + 生成），比纯 RAG 慢得多，
  // 后端单次 LLM 超时是 60s，所以这里要留够；默认的 0（不超时）会一直转圈。
  timeout: 180000,
})

const TOKEN_KEY = 'ekg_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

function toLogin() {
  setToken('')
  // 用 hash 跳转而不是 import router：拦截器里引 router 会形成循环依赖
  if (window.location.hash !== '#/login') window.location.hash = '#/login'
}

// 页面首屏常常并发好几个请求，token 过期时它们会**一起**失败。
// 不去重的话用户会看到同一句"登录已过期"连弹三四遍。
let expiryNotifiedAt = 0
const EXPIRY_NOTIFY_INTERVAL = 3000   // 3 秒内只提示一次

function notifyExpired(message) {
  const now = Date.now()
  const first = now - expiryNotifiedAt > EXPIRY_NOTIFY_INTERVAL
  expiryNotifiedAt = now
  if (first) ElMessage.error(message || '登录已过期，请重新登录')
  toLogin()   // 跳转无论如何都要做（幂等：已在登录页就不动）
}

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => {
    const body = resp.data
    // 非标准响应（比如拿到了静态文件）直接放行，避免误判
    if (!body || typeof body.code !== 'number') return body

    if (body.code === 0) return body

    if (body.code === 40100) {
      // 登录过期：清掉本地 token 并回登录页，不要在每个页面各写一遍
      notifyExpired(body.message)
    } else {
      // 其余错误码（40000 参数 / 40300 权限 / 5000x 服务端…）统一弹提示。
      // 后端已经把细节收敛成一句人话，这里直接展示即可。
      ElMessage.error(body.message || `请求失败（code=${body.code}）`)
    }
    return Promise.reject(new Error(body.message || 'business error'))
  },
  (err) => {
    // 走到这里说明连 HTTP 层都没成功：后端没起、代理没通、或请求超时
    const msg =
      err.code === 'ECONNABORTED'
        ? '请求超时：这个操作比较慢，请稍后重试'
        : import.meta.env.DEV
          ? '无法连接后端服务，请确认 uvicorn 已启动（127.0.0.1:8000）'
          : '暂时无法连接演示服务，请稍后重试'
    ElMessage.error(msg)
    return Promise.reject(err)
  }
)

export default http
