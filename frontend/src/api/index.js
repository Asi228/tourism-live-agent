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
  ask: (payload) => http.post('/chat', payload),
  // Agent：多步决策，返回执行轨迹；会多调几次模型，更慢也更贵
  askAgent: (payload) => http.post('/agent/chat', payload),
  // 只检索不给模型：用来对比"重排前后"的排序差异
  search: (payload) => http.post('/search', payload),
}

export const conversationApi = {
  // 没有"创建会话"接口：首次提问时后端隐式创建，新 id 在 /chat 的响应里
  list: () => http.get('/conversations'),
  detail: (id) => http.get(`/conversations/${id}`),
  remove: (id) => http.delete(`/conversations/${id}`),
}
