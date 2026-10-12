import http from './http'

// 把接口包成语义化函数：页面里写 `knowledgeApi.list()` 比写 http.get('/knowledge') 好读，
// 而且**接口路径只在这一个文件里出现** —— 后端改路径时只改这里。

export const authApi = {
  login: (payload) => http.post('/auth/login', payload),
  register: (payload) => http.post('/auth/register', payload),
  me: () => http.get('/auth/me'),
}

export const userApi = {
  // 仅系统管理员可调，用于成员授权时选人
  list: (keyword) => http.get('/users', { params: keyword ? { keyword } : {} }),
}

export const knowledgeApi = {
  list: () => http.get('/knowledge'),
  create: (payload) => http.post('/knowledge', payload),
  update: (id, payload) => http.put(`/knowledge/${id}`, payload),
  remove: (id) => http.delete(`/knowledge/${id}`),
  members: (id) => http.get(`/knowledge/${id}/members`),
  grant: (id, payload) => http.post(`/knowledge/${id}/members`, payload),
  revoke: (id, userId) => http.delete(`/knowledge/${id}/members/${userId}`),
  // 批量向量化整库（灌数据时最省事）
  vectorize: (id) => http.post(`/knowledge/${id}/vectorize`),
}

export const documentApi = {
  list: (kbId) => http.get('/documents', { params: { kb_id: kbId } }),
  detail: (id) => http.get(`/documents/${id}`),
  status: (id) => http.get(`/documents/${id}/status`),
  chunks: (id) => http.get(`/documents/${id}/chunks`),
  vectorize: (id) => http.post(`/documents/${id}/vectorize`),
  remove: (id) => http.delete(`/documents/${id}`),
  upload: (kbId, file, onProgress) => {
    const form = new FormData()
    form.append('kb_id', kbId)
    form.append('file', file)
    // 上传单独放宽超时：20MB 文件在慢速网络下可能超过默认值
    return http.post('/documents/upload', form, {
      timeout: 300000,
      onUploadProgress: onProgress,
    })
  },
}

export const chatApi = {
  // 纯 RAG：快、便宜，答案同样带引用
  // 第二参 config 透传 axios —— 降级重试靠它传 `signal`，
  // 否则用户切走之后那次整段请求仍会跑满 180s 并花掉一次付费调用。
  // 第三参 url 仅 dev-only 的 mock 用（见 views/Chat.vue 的 MOCK_MODE）：
  // mock 时必须连非流式也拦掉，否则"降级重试"会打到真后端、真花一次钱。
  ask: (payload, config, url) => http.post(url || '/chat', payload, config),
  // Agent：多步决策，返回执行轨迹；会多调几次模型，更慢也更贵
  askAgent: (payload, config, url) => http.post(url || '/agent/chat', payload, config),
  // 只检索不给模型：用来对比"重排前后"的排序差异
  search: (payload) => http.post('/search', payload),
}

export const conversationApi = {
  // 没有"创建会话"接口：首次提问时后端隐式创建，新 id 在 /chat 的响应里
  // params 传 { limit, offset } 翻页；不传就用后端默认（limit=50, offset=0）。
  // 这个接口是拿到更早会话的**唯一**入口，不翻页的话第 51 条之后就永远看不到。
  list: (params) => http.get('/conversations', { params }),
  // 详情默认只返回**最新一页**消息。往前翻传 { limit, before_id }：
  // before_id 是"当前最旧那条消息的 id"，用 keyset 而不是 offset ——
  // 会话尾部一直在长，offset 翻页会在翻页期间漏条/重条。
  detail: (id, params) => http.get(`/conversations/${id}`, { params }),
  remove: (id) => http.delete(`/conversations/${id}`),
}

export const memoryApi = {
  // 长期记忆（跨会话）：写入是**显式**的 —— 用户自己说"记住这件事"，后端不做自动提炼。
  // 注意：写进去就会存下来；但它**会不会拼进提示词**由服务端开关 USER_MEMORY_ENABLED 决定。
  list: () => http.get('/memories'),
  create: (content) => http.post('/memories', { content }),
  remove: (id) => http.delete(`/memories/${id}`),
}

// 流式问答单独放一个文件（`stream.js`）：它**不能用 axios** ——
// 拦截器整条建立在"响应体是统一信封"之上，而 SSE 是一帧帧事件。
// 所以它走原生 fetch，并自己补回 401 跳登录与错误提示。
export { streamChat } from './stream'
