import http from './http'

// 把接口包成语义化函数：页面里写 `knowledgeApi.list()` 比写 http.get('/knowledge') 好读，
// 而且**接口路径只在这一个文件里出现** —— 后端改路径时只改这里。

export const authApi = {
  // 公开的认证配置（无需登录）：目前只有"自助注册开没开"，
  // 登录页据此决定显不显示「注册」标签页
  config: () => http.get('/auth/config'),
  login: (payload) => http.post('/auth/login', payload),
  register: (payload) => http.post('/auth/register', payload),
  me: () => http.get('/auth/me'),
}

export const userApi = {
  // 仅系统管理员可调。
  // 默认最小字段（选人授权用）；`full=true` 时带邮箱与注册时间（用户管理页用）。
  list: (keyword, full) =>
    http.get('/users', { params: { ...(keyword ? { keyword } : {}), ...(full ? { full: true } : {}) } }),
  // 开通账号 —— 可指定初始角色（自助注册默认关，这是主要的开号路径）
  create: (payload) => http.post('/users', payload),
  // 停用 / 启用 —— `is_active=false` 会立刻拒登录，已签发的 token 下一次请求也会失效
  setStatus: (id, isActive) => http.patch(`/users/${id}/status`, { is_active: isActive }),
  // 重置密码 —— 旧密码与**该用户已签发的所有 token**一起失效（token 里带密码指纹）。
  // 响应里的 `is_self` 表示"重置的是你自己"，前端据此主动登出。
  resetPassword: (id, newPassword) =>
    http.patch(`/users/${id}/password`, { new_password: newPassword }),
  // 改角色 —— 提权入口，后端有三条约束（不能改自己 / 不能没有 admin / 必留审计）
  updateRole: (id, roleCode) => http.patch(`/users/${id}/role`, { role_code: roleCode }),
  // 从"人"这一侧看权限：他被授权了哪些库
  grants: (id) => http.get(`/users/${id}/grants`),
}

export const statsApi = {
  // 概览统计，**口径按当前用户可见范围**（员工看到的是他有权访问的那部分）
  overview: () => http.get('/stats/overview'),
  // 图表用的趋势数据（近 N 天提问数 + 各库文档数）。
  // 与 overview **分开取**：首屏不该为"补了 6 个 0 的折线"付流量，图表按需再要。
  trends: (days = 7) => http.get(`/stats/trends?days=${days}`),
}

export const auditApi = {
  // 管理操作审计（仅系统管理员）。
  // 翻页用 keyset（before_id = 当前最旧那条的 id）而不是 offset ——
  // 审计只追加，offset 会在翻的过程中漏条/重条。响应是 {items, has_more}。
  list: (params = {}) => http.get('/audit', { params }),
  // 动作清单（value + 中文说明）由后端下发，前端不维护第二份映射
  actions: () => http.get('/audit/actions'),
}

export const systemApi = {
  // 健康检查：MySQL / Milvus / Redis 逐项状态（后端有 3 秒缓存，连点不会反复打外部依赖）。
  // 这个端点**不需要登录**，但没有它前端就得靠"随便调一个接口看会不会失败"来判断后端死活。
  health: () => http.get('/health'),
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
  // 只检索不给模型：用来对比"重排前后"的排序差异（`/search` 默认 rerank=false）。
  // ⚠️ **当前没有 UI 入口**——这是刻意的，它是**对照实验**用的封装
  //（见 docs/benchmarks/query-rewrite.md 的做法），不是漏接线的界面功能。
  // 重跑实验时用它，或走调试台的「万能请求」直接 POST。
  search: (payload) => http.post('/search', payload),
}

export const vectorApi = {
  // 向量库自检：Milvus 版本、向量条数、**孤儿向量**（所属知识库已删却还留在 Milvus 里，
  // 会被检索命中却没有正文）、以及反向的"文档处理好了却一条向量都没有"。
  // 两者都会静默改变检索结果，而 documents 表里没有任何向量相关字段 —— 从界面上看不出来。
  // ⚠️ 仅系统管理员：后端 require_admin。
  health: () => http.get('/vector/health'),
  // 清理孤儿向量（**真删 Milvus 数据、不可逆**）。
  // 入口在「系统状态」页的向量库卡片上：**只在真有孤儿时才出现**那个按钮，
  // 且走确认框（与"少一个可点的地方就少一次手滑"同一个思路）。
  purgeOrphans: () => http.post('/vector/purge-orphans'),
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
