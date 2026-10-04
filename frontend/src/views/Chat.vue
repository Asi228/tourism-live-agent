<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { chatApi, knowledgeApi } from '../api'

const kbList = ref([])
const selectedKbs = ref([]) // 空 = 用当前用户全部可访问的库（**注意不能传空数组**，见 ask()）
const mode = ref('rag') // rag | agent
const query = ref('')
const asking = ref(false)
const messages = ref([])
const scroller = ref()

const MODE_LABEL = { rag: '纯 RAG', agent: 'Agent 多步' }

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

function buildPayload() {
  // 刻意**不传 top_k**：让后端的 RAG_TOP_K 配置生效。
  // 前端写死一个默认值等于把配置项架空 —— 之后在 .env 里改了它却不生效，还很难查出原因。
  const payload = { query: query.value.trim() }
  // ⚠️ 关键：没选库时**不要**传 kb_ids=[]。
  // 后端把"空数组"理解为"允许范围为空"→ 直接返回空结果（这是防越权的设计）；
  // 不传该字段才是"用我全部可访问的库"。这两个含义完全不同。
  if (selectedKbs.value.length) payload.kb_ids = selectedKbs.value
  return payload
}

async function ask() {
  if (!canAsk.value) return
  const text = query.value.trim()
  messages.value.push({ role: 'user', content: text, at: new Date() })
  query.value = ''
  asking.value = true
  await scrollToBottom()

  try {
    const body = mode.value === 'agent' ? await chatApi.askAgent(buildPayload()) : await chatApi.ask(buildPayload())
    messages.value.push({ role: 'assistant', data: body.data, mode: mode.value, at: new Date() })
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

function clearAll() {
  messages.value = []
  ElMessage.success('已清空当前对话（服务端本来就不存会话）')
}

function scorePercent(score) {
  // rerank 分数与余弦相似度都大致落在 0~1，展示成百分比更直观；
  // 负分（理论上向量余弦可以为负）夹到 0，避免进度条乱跳
  const v = Math.max(0, Math.min(1, Number(score) || 0))
  return Math.round(v * 100)
}

onMounted(loadKbList)
</script>

<template>
  <div class="chat-page">
    <!-- 顶部：知识库范围 + 模式 -->
    <el-card shadow="never" class="head-card">
      <div class="head-row">
        <el-select
          v-model="selectedKbs"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="检索范围：不选 = 我有权限的全部知识库"
          style="flex: 1; min-width: 260px"
        >
          <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>

        <el-radio-group v-model="mode">
          <el-radio-button value="rag">纯 RAG</el-radio-button>
          <el-radio-button value="agent">Agent 多步</el-radio-button>
        </el-radio-group>

        <el-button :disabled="!messages.length" @click="clearAll">清空</el-button>
      </div>
      <div class="mode-hint">{{ emptyHint }}</div>
    </el-card>

    <!-- 对话区 -->
    <el-card shadow="never" class="body-card">
      <div ref="scroller" class="scroller">
        <el-empty v-if="!messages.length" description="还没有提问">
          <div class="empty-tips">
            试试问：<br />
            「年假可以休几天」<br />
            「出差住宿费能报多少」
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

                <!-- 元信息：这一条是不同接口返回形状不完全一样，逐项判空 -->
                <div class="meta">
                  <el-tag v-if="m.data.answer_mode" size="small" :type="(ANSWER_MODE[m.data.answer_mode] || {}).type || 'info'">
                    {{ (ANSWER_MODE[m.data.answer_mode] || {}).text || m.data.answer_mode }}
                  </el-tag>
                  <el-tag size="small" :type="m.data.hit ? 'success' : 'info'">
                    {{ m.data.hit ? '有资料依据' : '未使用知识库资料' }}
                  </el-tag>
                  <span v-if="m.mode === 'rag'">
                    重排：{{ m.data.reranked ? '已重排' : '未重排' }}
                  </span>
                  <span v-if="m.mode === 'agent'">检索轮数：{{ m.data.iterations }}</span>
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

    <!-- 输入区 -->
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
        后端不保存会话（会话与记忆是后续阶段的事），刷新页面本次对话就会消失。
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
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
