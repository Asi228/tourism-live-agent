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

export const commerceApi = {
  products: () => http.get('/commerce/products'),
  createProduct: (payload) => http.post('/commerce/products', payload),
  createSku: (productId, payload) => http.post(`/commerce/products/${productId}/skus`, payload),
  sessions: () => http.get('/commerce/sessions'),
  session: (id) => http.get(`/commerce/sessions/${id}`),
  recap: (id) => http.get(`/commerce/sessions/${id}/recap`),
  createSession: (payload) => http.post('/commerce/sessions', payload),
  addSessionItem: (sessionId, payload) =>
    http.post(`/commerce/sessions/${sessionId}/items`, payload),
  createPackage: (sessionId) => http.post(`/commerce/sessions/${sessionId}/packages`),
  inspections: (sessionId) => http.get(`/commerce/sessions/${sessionId}/inspections`),
  runInspection: (sessionId) => http.post(`/commerce/sessions/${sessionId}/inspections`),
  inspection: (batchId) => http.get(`/commerce/inspections/${batchId}`),
  inspectionRules: () => http.get('/commerce/inspection-rules'),
  updateInspectionRule: (ruleCode, payload) =>
    http.patch(`/commerce/inspection-rules/${ruleCode}`, payload),
  inventory: (skuId) => http.get('/commerce/inventory', { params: skuId ? { sku_id: skuId } : {} }),
  createInventory: (payload) => http.post('/commerce/inventory', payload),
  promotions: (skuId) => http.get('/commerce/promotions', { params: skuId ? { sku_id: skuId } : {} }),
  createPromotion: (payload) => http.post('/commerce/promotions', payload),
  previewPrice: (payload) => http.post('/commerce/price-preview', payload),
  explainSession: (sessionId, payload) =>
    http.post(`/agent/live-sessions/${sessionId}/explain`, payload),
  approvalDraft: (sessionId, riskId) =>
    http.post(`/agent/live-sessions/${sessionId}/approval-draft`, { risk_id: riskId }),
}

// 自动化任务（RPA）：列表 / 详情 / 重新入队 + 开发期模拟平台数据
export const rpaApi = {
  metrics: (days = 30) => http.get('/rpa/metrics', { params: { days } }),
  monitoringPlans: () => http.get('/rpa/monitoring-plans'),
  saveMonitoringPlan: (sessionId, payload) =>
    http.put(`/rpa/monitoring-plans/${sessionId}`, payload),
  tasks: () => http.get('/rpa/tasks'),
  task: (id) => http.get(`/rpa/tasks/${id}`),
  createCollect: (sessionId) => http.post('/rpa/tasks', { session_id: sessionId }),
  requeue: (id) => http.post(`/rpa/tasks/${id}/requeue`),
  // 截图走带鉴权的端点（静态目录挂载会把"谁的证据"交出去）
  screenshotUrl: (taskId, stepId) => `/api/rpa/tasks/${taskId}/steps/${stepId}/screenshot`,
  mockSkus: () => http.get('/rpa/mock-platform/skus'),
  initMock: () => http.post('/rpa/mock-platform/initialize'),
  updateMockSku: (rowId, payload) => http.put(`/rpa/mock-platform/skus/${rowId}`, payload),
  // 高风险操作审批（改价 / 下架）：申请 → 决策（通过则生成唯一执行任务）
  approvals: () => http.get('/rpa/approvals'),
  createApproval: (payload) => http.post('/rpa/approvals', payload),
  decideApproval: (id, payload) => http.post(`/rpa/approvals/${id}/decision`, payload),
}

export const documentApi = {
  list: (kbId, filters = {}) => http.get('/documents', { params: { kb_id: kbId, ...filters } }),
  detail: (id) => http.get(`/documents/${id}`),
  status: (id) => http.get(`/documents/${id}/status`),
  chunks: (id) => http.get(`/documents/${id}/chunks`),
  vectorize: (id) => http.post(`/documents/${id}/vectorize`),
  remove: (id) => http.delete(`/documents/${id}`),
  // 上传白名单来自服务端（.env 的 ALLOWED_EXTENSIONS 是唯一权威）——
  // 前端不再自己维护一份会漂移的格式清单（评审 2026-10-15 指出：第 4 份清单）。
  uploadConfig: () => http.get('/documents/upload-config'),
  upload: (kbId, file, metadata = {}, onProgress) => {
    const form = new FormData()
    form.append('kb_id', kbId)
    form.append('file', file)
    Object.entries(metadata).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') form.append(key, value)
    })
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
  // 界面入口在「检索调试」页（仅管理员，views/SearchDebug.vue）；
  // 它也是对照实验用的封装（见 docs/benchmarks/query-rewrite.md 的做法）。
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
  // PATCH：两个字段都可选但至少给一个 —— 换内容传 { content }，切换开关传 { is_enabled }。
  // is_enabled=false 只是"暂时不拼进提示词"，数据仍在列表里，能开回来。
  update: (id, payload) => http.patch(`/memories/${id}`, payload),
  remove: (id) => http.delete(`/memories/${id}`),
}

// 答案反馈（Bad Case 闭环）。submit 是 **upsert**：同一条消息重复提交会更新，
// 不会报"你已经评价过了" —— 用户改主意是常态。
export const feedbackApi = {
  submit: (messageId, payload) => http.post(`/messages/${messageId}/feedback`, payload),
  get: (messageId) => http.get(`/messages/${messageId}/feedback`),
}

// 常见问题（FAQ 置顶）。**不进向量库、不参与检索** —— 命中即返回，
// 与 RAG 是两条独立的路，混进来会让"这个答案来自哪份文档"说不清。
export const faqApi = {
  list: (kbId) => http.get('/faqs', { params: { kb_id: kbId } }),
  create: (payload) => http.post('/faqs', payload),
  update: (id, payload) => http.patch(`/faqs/${id}`, payload),
  remove: (id) => http.delete(`/faqs/${id}`),
}

// 引用溯源：点答案里的 [n] → 取这块**完整**正文（列表里的 snippet 是截断过的）。
export const chunkApi = {
  get: (chunkId) => http.get(`/chunks/${chunkId}`),
}

// 流式问答单独放一个文件（`stream.js`）：它**不能用 axios** ——
// 拦截器整条建立在"响应体是统一信封"之上，而 SSE 是一帧帧事件。
// 所以它走原生 fetch，并自己补回 401 跳登录与错误提示。
export { streamChat } from './stream'
