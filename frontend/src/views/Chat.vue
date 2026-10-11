<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { conversationApi, knowledgeApi, memoryApi, streamChat } from '../api'

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
const convLoading = ref(false)
const convHasMore = ref(false)
// 一页 50 条（后端上限 100）。列表是拿到更早会话的唯一入口，所以必须能继续往下翻。
const CONV_PAGE_SIZE = 50
// 详情页每次取多少条消息。**必须分页**：长会话一次全量返回会把 sources/meta
// 三段 JSON 全吐出来，响应又大又慢。
const DETAIL_PAGE_SIZE = 50
const convHasOlder = ref(false)   // 这条会话在返回的这批之前还有更早的消息
const olderLoading = ref(false)

// ---- 长期记忆（Phase 11）----
// 与会话内的"上文"不是一回事：它是**跨会话**仍然成立的事实（"我在财务部"）。
// 写入必须显式（后端不做自动提炼），所以这里只给一个输入框 + 列表 + 删除。
const memories = ref([])
const memDraft = ref('')
const memSaving = ref(false)

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

/**
 * 拉会话列表。
 *
 * `append=true` 是"加载更多"：按当前条数当 offset 往后接。
 * 不做 append 时**整表重来**（重置成第一页）—— 提问后标题/活跃时间都会变，
 * 局部更新反而容易出现"顺序错了但看着正常"的问题。
 */
async function loadConversations({ append = false } = {}) {
  if (convLoading.value) return
  convLoading.value = true
  try {
    const offset = append ? conversations.value.length : 0
    const body = await conversationApi.list({ limit: CONV_PAGE_SIZE, offset })
    const rows = body.data || []
    conversations.value = append ? [...conversations.value, ...rows] : rows
    // 后端没有返回 total，用"这页是否装满"当依据：装满就认为可能还有下一页。
    // 代价是多一次必然为空的请求，换来的是不必为了一个 total 再加一次 count 查询。
    convHasMore.value = rows.length === CONV_PAGE_SIZE
  } catch {
    if (!append) conversations.value = []
    convHasMore.value = false
  } finally {
    convLoading.value = false
  }
}

// 进行中的流式请求（AbortController）。切换/新建会话、组件卸载时中止它 ——
// 这不是"能不能停止生成"的观感问题，是正确性问题：旧流的 done 回调会把
// conversation_id 写回 currentConvId，用户刚点开的空白界面会被后台那条旧流
// 顶回上一个会话，下一问就进了错误的会话。
let streamAbort = null

function abortStream() {
  if (streamAbort) {
    streamAbort.abort()
    streamAbort = null
  }
}

function newConversation() {
  abortStream()
  currentConvId.value = null
  messages.value = []
  convHasOlder.value = false
}

async function openConversation(id) {
  if (id === currentConvId.value) return
  if (asking.value) {
    ElMessage.warning('正在回答中，等这次答完再切换会话')
    return
  }
  currentConvId.value = id
  try {
    // 只取最新一页；更早的由"加载更早的消息"按需拉（before_id 游标）
    const body = await conversationApi.detail(id, { limit: DETAIL_PAGE_SIZE })
    messages.value = (body.data?.messages || []).map(toMessage)
    convHasOlder.value = body.data?.has_more === true
    await scrollToBottom()
  } catch {
    messages.value = []
    convHasOlder.value = false
  }
}

/**
 * 往更早翻一页，插到消息列表**头部**。
 *
 * 翻完要把滚动位置按住：插入内容会把下面的东西顶下去，不补这一下用户就"被跳走"了。
 * 做法是记录插入前后的 scrollHeight 差值，插完再把它加回 scrollTop。
 */
async function loadOlderMessages() {
  if (olderLoading.value || !messages.value.length) return
  // 游标取"最旧的、带服务端 id 的那条" —— 无 id 的是本次会话刚答、还没落库的几条，
  // 按不变量它们一定排在**尾部**，所以 find 到的就是当前最靠前的已知 id
  // （只有比它更早的才需要向后端拉）。别改成取最后一条：那会拿尾部的消息当游标。
  const oldest = messages.value.find((m) => m.id != null)
  if (!oldest) return

  olderLoading.value = true
  const box = scroller.value
  const heightBefore = box ? box.scrollHeight : 0
  try {
    const body = await conversationApi.detail(currentConvId.value, {
      limit: DETAIL_PAGE_SIZE,
      before_id: oldest.id,
    })
    const older = (body.data?.messages || []).map(toMessage)
    messages.value = [...older, ...messages.value]
    convHasOlder.value = body.data?.has_more === true
    await nextTick()
    if (box) box.scrollTop += box.scrollHeight - heightBefore
  } catch {
    /* 拦截器已提示 */
  } finally {
    olderLoading.value = false
  }
}

/**
 * 把服务端的历史消息还原成界面用的消息对象。
 *
 * assistant 消息在服务端拆成了三块：content（正文）、sources（引用）、
 * meta（当时的口径：模式 / 命中 / token / 轨迹…）。这里把它们拼回一个 `data`，
 * 这样"实时回答"和"回放历史"走**同一套渲染分支**，不会出现两套模板各写一遍。
 *
 * `id` 必须留着：它既是列表 key（往头部插更早的消息时不能靠下标），
 * 也是往前翻页的 keyset 游标（`before_id`）。
 */
function toMessage(m) {
  if (m.role === 'user') return { id: m.id, role: 'user', content: m.content }
  const meta = m.meta || {}
  return {
    id: m.id,
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

// ==================== 长期记忆 ====================

async function loadMemories() {
  try {
    const body = await memoryApi.list()
    memories.value = body.data || []
  } catch {
    memories.value = []
  }
}

async function addMemory() {
  const text = memDraft.value.trim()
  if (!text || memSaving.value) return
  memSaving.value = true
  try {
    const body = await memoryApi.create(text)
    // 后端按"最近更新在前"返回，新写的排最前，与列表顺序一致
    memories.value = [body.data, ...memories.value]
    memDraft.value = ''
    ElMessage.success('已记住')
  } catch {
    /* 拦截器已提示 */
  } finally {
    memSaving.value = false
  }
}

async function removeMemory(memory) {
  try {
    await ElMessageBox.confirm('删除这条长期记忆？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await memoryApi.remove(memory.id)
    memories.value = memories.value.filter((m) => m.id !== memory.id)
  } catch {
    /* 拦截器已提示 */
  }
}

// ==================== 提问 ====================

function buildPayload(text) {
  // 刻意**不传 top_k**：让后端的 RAG_TOP_K 配置生效。
  // 前端写死一个默认值等于把配置项架空 —— 之后在 .env 里改了它却不生效，还很难查出原因。
  const payload = { query: text }
  // ⚠️ 关键：没选库时**不要**传 kb_ids=[]。
  // 后端把"空数组"理解为"允许范围为空"→ 直接返回空结果（这是防越权的设计）；
  // 不传该字段才是"用我全部可访问的库"。这两个含义完全不同。
  if (selectedKbs.value.length) payload.kb_ids = selectedKbs.value
  // 带上会话 id 才能"接着上文问"；不带就是新开一段（后端会自动建）
  if (currentConvId.value) payload.conversation_id = currentConvId.value
  return payload
}

// 流式渲染的节流间隔：token 来得很密，每块都写进响应式对象会让 Vue 不停重渲染；
// 攒一小会儿再刷一次，肉眼看不出差别，渲染次数却能降一个数量级。
const STREAM_FLUSH_MS = 50

async function ask() {
  if (!canAsk.value) return
  const text = query.value.trim()
  messages.value.push({ role: 'user', content: text })
  // ⚠️ 先组 payload、再清空输入框：buildPayload 读的正是 query.value，
  // 顺序反了发出去的就是空问题（后端 min_length=1 直接 40000）。
  const payload = buildPayload(text)
  query.value = ''
  asking.value = true
  await scrollToBottom()

  try {
    await askStream(payload, mode.value === 'agent')
  } finally {
    asking.value = false
    await scrollToBottom()
  }
}

/**
 * 两条问答路共用同一套 SSE 渲染 —— RAG 逐字、Agent 逐节点，差别只在后端吐什么。
 *
 * 刻意不写成两份：节流、done 覆盖、三种中断语义、abort 守卫这些东西一旦有两份
 * 实现，迟早漂移；而漂移的表现是"某条路上偶发不提示"，很难归因。
 *
 * 必须处理的细节：
 *  1. **节流**（见 `STREAM_FLUSH_MS`）：新到的字先攒在普通变量里，定时器统一刷进
 *     响应式对象。Agent 不逐字，节流对它不起作用，共用一份也不受影响。
 *  2. **以 `done.answer` 为准**：逐块渲染可能因节流丢帧，服务端拼好的那段才是完整
 *     答案，最后覆盖一次 —— 保证"界面上看到的"与"历史里存的"逐字一致。
 *  3. **中断时留住已生成的部分**：直接换成错误提示会让内容"闪一下消失"，用户以为
 *     白等了。保留正文 + 一行说明，并**明说这次没进历史**。
 *  4. **每次请求一个 AbortController**：新建/删除会话、组件卸载时中止旧流，
 *     防止它的 done 回调把会话 id 写回一个用户已经离开的地方。
 *  5. **Agent 的答案是 `done` 里整段给的**（README 定了：它的价值在多步决策的进度
 *     而不是逐字输出）。所以 streaming 期间正文是空的，进度全靠 `step` 事件实时累加
 *     进 `data.steps` —— 模板里那条"执行轨迹"自己会长出来。
 *
 * @param {boolean} agentMode true 走 `/api/agent/chat/stream`
 */
async function askStream(payload, agentMode = false) {
  const slot = reactive({
    role: 'assistant',
    mode: agentMode ? 'agent' : 'rag',
    data: { answer: '', sources: [], hit: null, reranked: null, steps: [] },
    streaming: true,
  })
  messages.value.push(slot)

  const controller = new AbortController()
  streamAbort = controller

  let pending = ''
  let timer = null
  let lastScrollAt = 0

  const flush = () => {
    if (pending) {
      slot.data.answer += pending
      pending = ''
    }
  }
  const stopTimer = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  let ok = false
  // 流一旦开始，后端就**可能已经落库**了（落库发生在 `yield done` 之前，与客户端
  // 还连不连着无关）。所以"主动停止"和"连接被掐断"这两种也要刷会话列表 ——
  // 不刷的话库里已经多了个会话，界面上却看不到，得刷新页面才冒出来。
  let maybePersisted = false
  try {
    await streamChat(payload, {
      meta: (d) => {
        // 引用在答案之前就画出来 —— 用户先看到"依据来自哪几份文档"
        Object.assign(slot.data, {
          sources: d.sources || [],
          hit: d.hit,
          reranked: d.reranked,
          searched_kb_ids: d.searched_kb_ids,
          skipped_orphans: d.skipped_orphans,
          model: d.model,
        })
      },
      delta: (d) => {
        pending += d.text
        if (!timer) {
          timer = setInterval(() => {
            flush()
            // 滚动也跟着节流：每个 token 都贴底，用户想往回翻会被一直拽回来
            const now = Date.now()
            if (now - lastScrollAt > 120) {
              lastScrollAt = now
              scrollToBottom()
            }
          }, STREAM_FLUSH_MS)
        }
      },
      // Agent 专属：每跑完一个节点就吐一条轨迹，这里直接累进 `steps`。
      // 模板那条"执行轨迹（N 步）"折叠面板读的就是它 —— 于是标题会自己往上跳，
      // 用户能看见它卡在检索还是判断。RAG 侧永远不会来这个事件。
      step: (d) => {
        slot.data.steps.push(d)
        // 流式期间 meta 还没到，检索轮数先自己数着（done/meta 里的才是权威值）
        if (d.node === 'retrieve') slot.data.iterations = (slot.data.iterations || 0) + 1
      },
      done: (d) => {
        flush()
        slot.data.answer = d.answer
        if (d.usage) slot.data.usage = d.usage
        // Agent 的答案不是逐字来的：正文、轨迹、轮数都在 done 里一次性到，
        // 这里全部以服务端为准覆盖（step 期间攒的是"过程视图"）。
        if (agentMode) {
          if (d.steps?.length) slot.data.steps = d.steps
          slot.data.iterations = d.iterations
          slot.data.elapsed_ms = d.elapsed_ms
        }
        // 与历史回放同源：后端把 finish_reason 写进了 meta，前端展开进 data，
        // 所以模板里"当场答的"与"翻出来的老消息"用同一个判据（见 truncated 分支）。
        slot.data.finish_reason = d.finish_reason
        // 后端只在"答案完整返回、但落库失败"时给 saved=false ——
        // 这是用户知道"这轮没进历史"的唯一通道，必须提示出来。
        if (d.saved === false) slot.unsaved = true
        // 写会话 id 前先确认这条请求还是"当前那条"：用户可能已经点了
        // "新对话"（abort），此时写回去会把刚清空的界面又指向旧会话。
        if (d.conversation_id && !controller.signal.aborted) {
          currentConvId.value = d.conversation_id
        }
      },
    }, {
      signal: controller.signal,
      url: agentMode ? '/api/agent/chat/stream' : '/api/chat/stream',
    })
    ok = true
  } catch (e) {
    flush()
    if (e?.name === 'AbortError') {
      // 用户主动停止。后端落库发生在 yield done 之前，停止的瞬间可能刚好已经落库，
      // 前端无从判断 —— 所以文案保持中性，不下"存没存"的结论。
      slot.stopped = true
      maybePersisted = true
    } else if (slot.data.answer && e?.code === 'STREAM_TRUNCATED') {
      // 连接被掐断（反代空闲超时 / 网络抖动）：字节没送到，但**后端多半已经落完库**了 ——
      // 落库不看客户端还在不在。所以这里不能用"未存入历史"那句，那是流内 error 的口径。
      slot.cutoff = true
      maybePersisted = true
    } else if (slot.data.answer) {
      slot.interrupted = true // 流内 error：后端明确不落库，可以下这个结论
    } else {
      slot.error = true
      slot.content = e?.message || '请求失败，请稍后重试'
    }
  } finally {
    stopTimer()
    flush()
    slot.streaming = false
    if (streamAbort === controller) streamAbort = null
  }
  // 刷新列表放 try/catch 外（理由同 askOnce）：标题与活跃时间都变了
  if (ok || maybePersisted) await loadConversations()
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
  await Promise.all([loadKbList(), loadConversations(), loadMemories()])
})

// 离开页面时别让流还挂着：它占着一条连接和一个后端线程，done 回调还会写已卸载组件的状态
onBeforeUnmount(() => abortStream())
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
        <el-button
          v-if="convHasMore"
          text
          size="small"
          class="conv-more"
          :loading="convLoading"
          @click="loadConversations({ append: true })"
        >加载更多</el-button>
      </div>

      <!-- 长期记忆（跨会话）。写进去就存下了；会不会拼进提示词由服务端开关决定。 -->
      <div class="mem-block">
        <div class="mem-head">
          <span>我的记忆</span>
          <span class="mem-count" v-if="memories.length">{{ memories.length }}</span>
        </div>
        <div class="mem-add">
          <el-input
            v-model="memDraft"
            size="small"
            placeholder="记住一件事，如：我在财务部"
            maxlength="200"
            @keyup.enter="addMemory"
          />
          <el-button
            size="small"
            :disabled="!memDraft.trim()"
            :loading="memSaving"
            @click="addMemory"
          >记住</el-button>
        </div>
        <div class="mem-list">
          <div v-for="m in memories" :key="m.id" class="mem-item">
            <span class="mem-text" :title="m.content">{{ m.content }}</span>
            <span class="mem-del" title="删除" @click="removeMemory(m)">×</span>
          </div>
          <div v-if="!memories.length" class="mem-empty">
            还没有记忆。写一条会在后续问答里作为背景带上（需服务端开启长期记忆开关）
          </div>
        </div>
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

          <div v-if="convHasOlder" class="older-wrap">
            <el-button text size="small" :loading="olderLoading" @click="loadOlderMessages">
              加载更早的消息
            </el-button>
          </div>

          <div
            v-for="(m, i) in messages"
            :key="m.id != null ? 's' + m.id : 'l' + i"
            class="msg"
            :class="m.role"
          >
            <template v-if="m.role === 'user'">
              <div class="bubble user-bubble">{{ m.content }}</div>
            </template>

            <template v-else>
              <div class="bubble agent-bubble">
                <template v-if="m.error">
                  <span class="err">{{ m.content }}</span>
                </template>

                <template v-else>
                  <div class="answer">
                    {{ m.data.answer }}<span v-if="m.streaming && m.mode === 'rag'" class="caret"></span>
                  </div>
                  <div v-if="m.interrupted" class="stream-note">
                    生成中断，本次未存入历史（半截答案不进历史，免得影响后续追问）
                  </div>
                  <div v-else-if="m.stopped" class="stream-note">
                    已停止生成（本次回答可能未存入历史）
                  </div>
                  <div v-else-if="m.cutoff" class="stream-note">
                    连接中断，回答可能不完整（已生成的部分多半已经存进历史了）
                  </div>
                  <div v-else-if="m.unsaved" class="stream-note">
                    答案已完整返回，但这次没能存入历史
                  </div>
                  <!-- 被 max_tokens 截断：与上面几条**独立**判断（可能叠加出现）。
                       判据来自 meta 里的 finish_reason，当场答的与翻出来的老消息同源。 -->
                  <div v-if="m.data.finish_reason === 'length'" class="stream-note">
                    答案达到长度上限被截断，后面的内容没生成完 —— 换个问法或让他分段回答
                  </div>

                  <!-- 元信息：接口不同、回放历史与实时回答的字段也略有差异，逐项判空 -->
                  <div class="meta">
                    <el-tag v-if="m.data.answer_mode" size="small" :type="(ANSWER_MODE[m.data.answer_mode] || {}).type || 'info'">
                      {{ (ANSWER_MODE[m.data.answer_mode] || {}).text || m.data.answer_mode }}
                    </el-tag>
                    <!-- 脱敏消息：当时的口径全都取不回来了（meta 只剩 source），
                         不能说成"未使用知识库资料" —— 它其实用过资料，只是现在无权看了 -->
                    <el-tag v-if="m.data.redacted" size="small" type="warning">资料已无权访问</el-tag>
                    <!-- 流式中 meta 事件还没到，hit 还是 null。
                         这时不能落进下面那个三元 —— 会把"还没检索完"说成"未使用知识库资料"。 -->
                    <el-tag v-else-if="m.data.hit === null" size="small" type="info">检索中…</el-tag>
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

/* ---- 长期记忆 ---- */
.mem-block {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 8px;
}

.mem-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.mem-count {
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.mem-add {
  display: flex;
  gap: 6px;
}

.mem-list {
  max-height: 160px;
  overflow-y: auto;
}

.mem-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 2px;
  font-size: 12px;
  line-height: 1.6;
}

/* 记忆可能很长，列表里最多显示两行；完整内容靠 title 悬浮看 */
.mem-text {
  flex: 1;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}

.mem-del {
  flex: none;
  cursor: pointer;
  color: var(--el-text-color-secondary);
}

.mem-del:hover {
  color: var(--el-color-danger);
}

.mem-empty {
  padding: 6px 2px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
.conv-more {
  width: 100%;
  margin-top: 4px;
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

/* "加载更早的消息"：只在详情返回 has_more=true 时出现，居中放在消息列表顶部 */
.older-wrap {
  display: flex;
  justify-content: center;
  margin-bottom: 8px;
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
/* 流式生成中的光标。用闪烁的竖条而不是转圈：转圈会让人以为整个请求还没开始，
   而这里的事实是"答案正在一个个字出来"。 */
.caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--el-color-primary);
  animation: caret-blink 1s steps(2, start) infinite;
}
@keyframes caret-blink {
  to {
    visibility: hidden;
  }
}
/* 流中途断了：正文保留，只在下面加一行说明（并说清没进历史）。 */
.stream-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-warning);
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
