/**
 * Chat.vue 的**纯逻辑**：提问载荷拼装、历史消息整形、会话标题。
 *
 * 为什么从 .vue 里搬出来：这三段是"栽过跟头"的地方（载荷少传一个字段、
 * 消息形状对不上、标题回退错了），而 .vue 里的逻辑 `node --test` 碰不到。
 * 搬进 utils 后它们才有测试守着（`npm test`）。
 * 与 answerMode / exportMarkdown 同一套做法。
 */

/**
 * 拼提问载荷。三个"不传"都是刻意的：
 *
 * 1. **不传 top_k** —— 让后端的 RAG_TOP_K 配置生效。前端写死默认值等于
 *    把配置项架空：之后在 .env 里改了它却不生效，还很难查出原因；
 * 2. **没选库时不传 kb_ids** —— 后端把"空数组"理解为"允许范围为空"，
 *    会直接返回空结果（这是防越权的设计）；不传该字段才是
 *    "用我全部可访问的库"。这两个含义完全不同，传错就是静默搜不到东西；
 * 3. **没有会话 id 时不传 conversation_id** —— 后端会在首次提问时建会话。
 *
 * @param {string} text 用户输入
 * @param {{ selectedKbs?: number[], conversationId?: number|null }} ctx
 */
export function buildChatPayload(text, ctx = {}) {
  const payload = { query: text }
  const selectedKbs = ctx.selectedKbs || []
  if (selectedKbs.length) payload.kb_ids = selectedKbs
  const conversationId = ctx.conversationId
  if (conversationId) payload.conversation_id = conversationId
  return payload
}

/**
 * 后端消息 → 界面消息形状。
 *
 * assistant 消息的 `mode` 由 `meta.source` 决定（agent / rag）——
 * 它决定消息气泡用哪套渲染（Agent 才显示"执行轨迹"）。
 * `data` 是模板的渲染面：答案 + sources + 展开的 meta
 * （answer_mode / hit / finish_reason 这些标签全靠 meta 展开进去）。
 */
export function toChatMessage(m) {
  if (m.role === 'user') return { id: m.id, role: 'user', content: m.content }
  const meta = m.meta || {}
  return {
    id: m.id,
    role: 'assistant',
    mode: meta.source === 'agent' ? 'agent' : 'rag',
    data: { answer: m.content, sources: m.sources || [], ...meta },
  }
}

/**
 * 当前会话标题：没开始就叫"新对话"；列表里找到了用列表标题
 * （后端可能已按首条提问生成过）；找不到（刚建、列表还没刷）退回 id 兜底 ——
 * 不能显示空白，用户会以为界面坏了。
 */
export function conversationTitle(currentConvId, conversations) {
  if (!currentConvId) return '新对话'
  const hit = (conversations || []).find((c) => c.id === currentConvId)
  return hit ? hit.title : `对话 ${currentConvId}`
}
