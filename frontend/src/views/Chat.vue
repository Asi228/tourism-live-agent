<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { chatApi, conversationApi, knowledgeApi } from '../api'

const kbList = ref([])
const selectedKbs = ref([]) // 空 = 用当前用户全部可访问的库（**注意不能传空数组**，见 buildPayload）
const mode = ref('rag') // rag | agent
const query = ref('')
const asking = ref(false)
const messages = ref([])
const scroller = ref()

// ---- 会话（Phase 9）----
const conversations = ref([])
const currentConvId = ref(null) // null = 还没开始（或刚点了"新对话"），后端会在首次提问时建

// 后端 `answer_mode` 的三种取值 → 标签样式与话术
const ANSWER_MODE = {
  grounded: { text: '依据资料', type: 'success' },
  no_evidence: { text: '无可用资料', type: 'warning' },
  smalltalk: { text: '寒暄直答', type: 'info' },
}

const canAsk = computed(() => !!query.value.trim() && !asking.value)

const emptyHint = computed(() =>
  mode.value === 'agent'
    ? 'Agent 会先判断要不要查库，查到后再评估资料够不够，不够就换个关键词再查一次 —— 比纯 RAG 更慢也更贵，但寒暄类问题会更自然、更省。'
    : '纯 RAG：检索一次就生成，快且便宜。答案只依据检索到的资料，并带引用编号。'
)

async function loadKbList() {
  try {
    const body = await knowledgeApi.list()
    kbList.value = body.data || []
  } catch {
    kbList.value = []
  }
}

// ==================== 会话列表 ====================

async function loadConversations() {
  try {
    const body = await conversationApi.list()
    conversations.value = body.data || []
  } catch {
    conversations.value = []
  }
}

function newConversation() {
  currentConvId.value = null
  messages.value = []
}

async function openConversation(id) {
  if (id === currentConvId.value) return
  if (asking.value) {
    ElMessage.warning('正在回答中，等这次答完再切换会话')
    return
  }
  currentConvId.value = id
  try {
    const body = await conversationApi.detail(id)
    messages.value = (body.data?.messages || []).map(toMessage)
    await scrollToBottom()
  } catch {
    messages.value = []
  }
}

/**
 * 把服务端的历史消息还原成界面用的消息对象。
 *
 * assistant 消息在服务端拆成了三块：content（正文）、sources（引用）、
 * meta（当时的口径：模式 / 命中 / token / 轨迹…）。这里把它们拼回一个 `data`，
 * 这样"实时回答"和"回放历史"走**同一套渲染分支**，不会出现两套模板各写一遍。
 */
function toMessage(m) {
  if (m.role === 'user') return { role: 'user', content: m.content }
  const meta = m.meta || {}
  return {
    role: 'assistant',
    mode: meta.source === 'agent' ? 'agent' : 'rag',
    data: { answer: m.content, sources: m.sources || [], ...meta },
  }
}

async function removeConversation(conv) {
  try {
    await ElMessageBox.confirm(`删除对话「${conv.title}」及其全部消息？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await conversationApi.remove(conv.id)
    ElMessage.success('已删除')
    if (currentConvId.value === conv.id) newConversation()
    await loadConversations()
  } catch {
    /* 拦截器已提示 */
  }
}

function formatTime(s) {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : `${d.getMonth() + 1}/${d.getDate()}`
}

// ==================== 提问 ====================

function buildPayload() {
  // 刻意**不传 top_k**：让后端的 RAG_TOP_K 配置生效。
  // 前端写死一个默认值等于把配置项架空 —— 之后在 .env 里改了它却不生效，还很难查出原因。
  const payload = { query: query.value.trim() }
  // ⚠️ 关键：没选库时**不要**传 kb_ids=[]。
  // 后端把"空数组"理解为"允许范围为空"→ 直接返回空结果（这是防越权的设计）；
  // 不传该字段才是"用我全部可访问的库"。这两个含义完全不同。
  if (selectedKbs.value.length) payload.kb_ids = selectedKbs.value
  // 带上会话 id 才能"接着上文问"；不带就是新开一段（后端会自动建）
  if (currentConvId.value) payload.conversation_id = currentConvId.value
  return payload
}

async function ask() {
  if (!canAsk.value) return
  const text = query.value.trim()
  messages.value.push({ role: 'user', content: text })
  query.value = ''
  asking.value = true
  await scrollToBottom()

  try {
    const body = mode.value === 'agent' ? await chatApi.askAgent(buildPayload()) : await chatApi.ask(buildPayload())
    const data = body.data
    messages.value.push({ role: 'assistant', data, mode: mode.value })

    // 后端可能在这次提问时刚建了会话 —— 记下来，并刷新列表（标题来自首问、活跃时间也变了）
    if (data.conversation_id) {
      currentConvId.value = data.conversation_id
      await loadConversations()
    }
  } catch {
    // 失败也留一条痕迹，否则用户会以为没发出去
    messages.value.push({ role: 'assistant', error: true, content: '请求失败，请稍后重试' })
  } finally {
    asking.value = false
    await scrollToBottom()
  }
}

async function scrollToBottom() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

function scorePercent(score) {
  // rerank 分数与余弦相似度都大致落在 0~1，展示成百分比更直观；
  // 负分（理论上向量余弦可以为负）夹到 0，避免进度条乱跳
  const v = Math.max(0, Math.min(1, Number(score) || 0))
  return Math.round(v * 100)
}

onMounted(async () => {
  await Promise.all([loadKbList(), loadConversations()])
})
</script>

<template>
  <div class="chat-page">
    <!-- 左：会话列表 -->
    <aside class="side">
      <el-button type="primary" class="new-btn" @click="newConversation">＋ 新对话</el-button>
      <div class="conv-list">
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === currentConvId }"
          @click="openConversation(c.id)"
        >
          <div class="conv-title">{{ c.title }}</div>
          <div class="conv-meta">
            <span>{{ c.message_count }} 条 · {{ formatTime(c.updated_at) }}</span>
            <span class="conv-del" title="删除" @click.stop="removeConversation(c)">×</span>
          </div>
        </div>
        <div v-if="!conversations.length" class="conv-empty">还没有对话，直接在右边提问即可</div>
      </div>
    </aside>

    <!-- 右：对话区 -->
    <div class="main-col">
      <el-card shadow="never" class="head-card">
        <div class="head-row">
          <el-select
            v-model="selectedKbs"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="检索范围：不选 = 我有权限的全部知识库"
            style="flex: 1; min-width: 240px"
          >
            <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
          </el-select>

          <el-radio-group v-model="mode">
            <el-radio-button value="rag">纯 RAG</el-radio-button>
            <el-radio-button value="agent">Agent 多步</el-radio-button>
          </el-radio-group>
        </div>
        <div class="mode-hint">{{ emptyHint }}</div>
      </el-card>

      <el-card shadow="never" class="body-card">
        <div ref="scroller" class="scroller">
          <el-empty v-if="!messages.length" description="还没有提问">
            <div class="empty-tips">
              试试问：<br />
              「年假可以休几天」<br />
              「出差住宿费能报多少」<br />
              <span class="tips-note">同一段对话会记住上文，可以直接追问「那病假呢」</span>
            </div>
          </el-empty>

          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <template v-if="m.role === 'user'">
              <div class="bubble user-bubble">{{ m.content }}</div>
            </template>

            <template v-else>
              <div class="bubble agent-bubble">
                <template v-if="m.error">
                  <span class="err">{{ m.content }}</span>
                </template>

                <template v-else>
                  <div class="answer">{{ m.data.answer }}</div>

                  <!-- 元信息：接口不同、回放历史与实时回答的字段也略有差异，逐项判空 -->
                  <div class="meta">
                    <el-tag v-if="m.data.answer_mode" size="small" :type="(ANSWER_MODE[m.data.answer_mode] || {}).type || 'info'">
                      {{ (ANSWER_MODE[m.data.answer_mode] || {}).text || m.data.answer_mode }}
                    </el-tag>
                    <!-- 脱敏消息：当时的口径全都取不回来了（meta 只剩 source），
                         不能说成"未使用知识库资料" —— 它其实用过资料，只是现在无权看了 -->
                    <el-tag v-if="m.data.redacted" size="small" type="warning">资料已无权访问</el-tag>
                    <el-tag v-else size="small" :type="m.data.hit ? 'success' : 'info'">
                      {{ m.data.hit ? '有资料依据' : '未使用知识库资料' }}
                    </el-tag>
                    <span v-if="m.mode === 'rag' && !m.data.redacted">
                      重排：{{ m.data.reranked ? '已重排' : '未重排' }}
                    </span>
                    <span v-if="m.mode === 'agent' && m.data.iterations != null">检索轮数：{{ m.data.iterations }}</span>
                    <span v-if="m.data.model">模型：{{ m.data.model }}</span>
                    <span v-if="m.data.usage">tokens：{{ m.data.usage.total_tokens }}</span>
                    <span v-if="m.data.elapsed_ms != null">耗时：{{ m.data.elapsed_ms }}ms</span>
                    <!-- 这个字段有三种语义，展示要分开：
                         数组非空 = 真实检索范围；null = 管理员不受限；空数组 = 本次没检索。
                         （空数组在 JS 里是 truthy，直接 v-if 会渲染成"检索范围："后面空着） -->
                    <span v-if="m.data.searched_kb_ids?.length">
                      检索范围：{{ m.data.searched_kb_ids.join('、') }}
                    </span>
                    <span v-else-if="m.data.searched_kb_ids === null">检索范围：全部库（管理员不受限）</span>
                    <span v-if="m.data.skipped_orphans">⚠ 跳过孤儿向量 {{ m.data.skipped_orphans }} 条</span>
                  </div>

                  <!-- Agent 轨迹：这是"它自己怎么决定走的"唯一证据 -->
                  <el-collapse v-if="m.mode === 'agent' && m.data.steps?.length" class="trace">
                    <el-collapse-item :title="`执行轨迹（${m.data.steps.length} 步）`">
                      <el-timeline>
                        <el-timeline-item
                          v-for="(s, si) in m.data.steps"
                          :key="si"
                          :timestamp="`${s.ms}ms`"
                          placement="top"
                        >
                          <b>{{ s.node }}</b>
                          <span class="trace-detail">
                            <template v-for="(v, k) in s" :key="k">
                              <template v-if="k !== 'node' && k !== 'ms'"> · {{ k }}={{ v }}</template>
                            </template>
                          </span>
                        </el-timeline-item>
                      </el-timeline>
                    </el-collapse-item>
                  </el-collapse>

                  <!-- 引用来源：编号与答案里的 [n] 一一对应 -->
                  <div v-if="m.data.sources?.length" class="sources">
                    <div class="sources-title">引用来源</div>
                    <div v-for="s in m.data.sources" :key="s.chunk_id" class="source">
                      <div class="source-head">
                        <el-tag size="small" type="primary">[{{ s.index }}]</el-tag>
                        <span class="doc-name">{{ s.document_name }}</span>
                        <span v-if="s.page_number != null" class="page">第 {{ s.page_number }} 页</span>
                        <span class="score">相似度 {{ scorePercent(s.score) }}%</span>
                      </div>
                      <div class="snippet">{{ s.snippet }}</div>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </div>

          <div v-if="asking" class="msg assistant">
            <div class="bubble agent-bubble asking">
              <!-- 用纯 CSS 的跳动点，不引 @element-plus/icons-vue ——
                   图标是独立包，为一个 loading 效果再装一个依赖不划算 -->
              <span class="dot" />
              <span class="dot" />
              <span class="dot" />
              {{ mode === 'agent' ? 'Agent 正在多步推理…' : '正在检索并生成…' }}
            </div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="input-card">
        <div class="input-row">
          <el-input
            v-model="query"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="输入你的问题，Enter 发送（Shift+Enter 换行）"
            @keydown.enter.exact.prevent="ask"
          />
          <el-button type="primary" size="large" :loading="asking" :disabled="!canAsk" @click="ask">
            提问
          </el-button>
        </div>
        <div class="foot-hint">
          <template v-if="currentConvId">
            对话已保存（id={{ currentConvId }}）—— 左侧列表里可以随时翻回来接着问。
          </template>
          <template v-else>首次提问会自动建一段新对话，之后就能追问上文了。</template>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  gap: 12px;
  height: 100%;
}
/* ---- 会话列表 ---- */
.side {
  width: 220px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.new-btn {
  width: 100%;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 6px;
}
.conv-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
}
.conv-item:hover {
  background: var(--el-fill-color-light);
}
.conv-item.active {
  background: var(--el-color-primary-light-9);
}
.conv-title {
  font-size: 13px;
  line-height: 1.4;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.conv-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.conv-del {
  padding: 0 4px;
  border-radius: 3px;
  visibility: hidden;
}
.conv-item:hover .conv-del {
  visibility: visible;
}
.conv-del:hover {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.conv-empty {
  padding: 12px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}

/* ---- 右侧对话区 ---- */
.main-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.head-card :deep(.el-card__body),
.input-card :deep(.el-card__body) {
  padding: 12px 16px;
}
.head-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.mode-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.body-card {
  flex: 1;
  overflow: hidden;
}
.body-card :deep(.el-card__body) {
  height: 100%;
  padding: 0;
}
.scroller {
  height: 100%;
  overflow-y: auto;
  padding: 16px;
}
.empty-tips {
  margin-top: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.9;
}
.tips-note {
  font-size: 12px;
}
.msg {
  display: flex;
  margin-bottom: 14px;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}
.bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.75;
}
.user-bubble {
  background: var(--el-color-primary);
  color: #fff;
  white-space: pre-wrap;
  word-break: break-word;
}
.agent-bubble {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
}
.asking {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: bounce 1.2s infinite ease-in-out;
}
.dot:nth-child(2) {
  animation-delay: 0.15s;
}
.dot:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes bounce {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  40% {
    transform: translateY(-5px);
    opacity: 1;
  }
}
.answer {
  white-space: pre-wrap;
  word-break: break-word;
}
.err {
  color: var(--el-color-danger);
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.trace {
  margin-top: 8px;
}
.trace-detail {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.sources {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.sources-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.source {
  border-left: 2px solid var(--el-color-primary-light-5);
  padding: 4px 0 4px 10px;
  margin-bottom: 8px;
}
.source-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.doc-name {
  font-weight: 600;
}
.page,
.score {
  color: var(--el-text-color-secondary);
}
.snippet {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.foot-hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
