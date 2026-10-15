<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { chatApi, chunkApi, conversationApi, faqApi, feedbackApi, knowledgeApi, streamChat } from '../api'
import ConversationList from '../components/ConversationList.vue'
import MemoryPanel from '../components/MemoryPanel.vue'
// 三处**纯逻辑**搬进 utils/，这样它们能被 `node --test` 直接跑（`npm test`）——
// 今天栽过的跟头（answer_mode 漏接线、导出缺分页提示、开关翻错值）全长在这三块里。
import { answerModeTag } from '../utils/answerMode'
import { buildConversationMarkdown, formatStamp, sourceScoreText } from '../utils/exportMarkdown'
import { buildChatPayload, conversationTitle as conversationTitleOf, toChatMessage } from '../utils/chatView'

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
// `openConversation` 的竞态令牌：每次切会话自增，请求回来时对不上就丢弃。
// 详见该函数里的说明（`asking` 守卫拦不住"快速连点两个会话"）。
let openSeq = 0
const olderLoading = ref(false)


// 「答案性质」的展示映射表搬去了 `src/utils/answerMode.js`（`npm test` 守着它）。

// `status` 事件里的 phase → 界面文案。与 `utils/answerMode.js` 的映射同一套用法：
// 后端新增 phase 时这里补一行就行，**绝不在模板里写死** ——
// 否则后端哪天吐 phase:"generating"，界面会拿一句"正在判定要不要查库"撒谎。
const PHASE_TEXT = {
  start: '正在判定要不要查库…',
}

// ==================== dev-only：SSE 故障模拟 ====================
// 网址带 `?mock=<模式>` 时，**流式和非流式都**改走 vite 的 mock 中间件
// （`frontend/mock-sse.js`），用来复现"断流 / 502 / 流内 error / 降级也失败"
// 这些只有真机才能触发的场景 —— 流式是问答在界面上的唯一入口，降级重试是它的
// 兜底，而这条路径没有自动化测试。
//
// 🔴 **必须用 import.meta.env.DEV 门控**：vite 会在 build 时把这个常量静态替换成
// false，于是 `MOCK_MODE` 恒为 null、`isDev` 恒为 false —— 两条 mock 路径在**运行时
// 都不可达**。少了它，线上任何人加 `?mock=break` 都会让页面去请求
// `/__mock__/chat/stream` → nginx 404 → !resp.ok → transport → 自动降级打真后端
// —— 等于给线上开了一条"一个 query 参数让每次问答白跑一趟"的路。
// （注意："build 产物里没有 mock 模块"和"运行时不受影响"是两回事，前者不蕴含后者。）
// ⚠️ 实话实说：模板分支与 helper 的**字符串仍会留在产物里**（Vue 先编译模板、
// rollup 才替换常量，跨不过这一层），量级几百字节。行为上无差别，但别把它说成
// "整条不进产物"。
const MOCK_MODE = import.meta.env.DEV
  ? new URLSearchParams(location.search).get('mock')
  : null

/** mock 模式下的非流式落点。也要走 mock，否则降级重试打的是真后端。 */
function mockNonStreamUrl(agentMode) {
  return `/__mock__/${agentMode ? 'agent/' : ''}chat`
}

function streamUrl(agentMode) {
  if (!MOCK_MODE) {
    return agentMode ? '/api/agent/chat/stream' : '/api/chat/stream'
  }
  return `/__mock__/${agentMode ? 'agent/' : ''}chat/stream?mode=${MOCK_MODE}`
}

/** mock 模式切换：改 query 并重载。dev-only，见上面的说明。 */
function setMock(mode) {
  const q = new URLSearchParams(location.search)
  if (mode) q.set('mock', mode)
  else q.delete('mock')
  location.search = q.toString()
}

const MOCK_BUTTONS = [
  { mode: 'normal', label: '基准' },
  { mode: 'break', label: '吐 2 帧后断流' },
  { mode: 'http502', label: '反代 502' },
  { mode: 'error', label: '流内 error' },
  { mode: 'hang', label: '首帧前挂 3 秒' },
  { mode: 'unsaved', label: '落库失败' },
  { mode: 'truncated', label: '答案被截断' },
  { mode: 'withconv', label: '带会话 id' },
]

const canAsk = computed(() => !!query.value.trim() && !asking.value)

// dev-only 工具条的开关。vite 在 build 时把它静态替换为 false → 运行时不渲染。
// （同类说明见上面 MOCK_MODE：运行时不可达，但模板分支的字符串仍会进产物。）
const isDev = import.meta.env.DEV

const emptyHint = computed(() => {
  const base = mode.value === 'agent'
    ? 'Agent 会先判断要不要查库，查到后再评估资料够不够，不够就换个关键词再查一次 —— 比纯 RAG 更慢也更贵，但寒暄类问题会更自然、更省。'
    : '纯 RAG：检索一次就生成，快且便宜。答案只依据检索到的资料，并带引用编号。'
  // mock 模式下必须让人看见"现在连的不是真后端"，否则会误以为系统坏了
  return MOCK_MODE ? `⚠️ SSE 故障模拟中（${MOCK_MODE}）—— 这条请求不会打到后端。${base}` : base
})

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
  // ⚠️ **竞态令牌**：`asking` 守卫只拦"回答中"，拦不住"快速连点两个会话"。
  // 那种情况下两次 `detail` 请求并发，谁后回来谁说了算 —— 点 A 再点 B 时若 A 后到，
  // 就会变成 currentConvId=B 而列表里是 A 的消息（答非所问，且看不出哪里错了）。
  // 每次切换自增一次，回来时对不上就整段丢弃。
  const seq = ++openSeq
  try {
    // 只取最新一页；更早的由"加载更早的消息"按需拉（before_id 游标）
    const body = await conversationApi.detail(id, { limit: DETAIL_PAGE_SIZE })
    if (seq !== openSeq) return // 期间用户又切走了，这份结果作废
    messages.value = (body.data?.messages || []).map(toMessage)
    convHasOlder.value = body.data?.has_more === true
    await scrollToBottom()
  } catch {
    if (seq !== openSeq) return
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
  return toChatMessage(m)
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


// ==================== 提问 ====================

function buildPayload(text) {
  // 搬去了 utils/chatView.js（`npm test` 守着那两个"不传"的坑：
  // 不写死 top_k、没选库不传空数组）—— 这里只做接线。
  return buildChatPayload(text, {
    selectedKbs: selectedKbs.value,
    conversationId: currentConvId.value,
  })
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
    data: { answer: '', sources: [], hit: null, reranked: null, steps: [], answer_mode: null },
    streaming: true,
    // ⑤ `status` 事件点亮它：非空表示"后端已开工、但第一个节点还没回来"
    phase: null,
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
  // ④ 是否收到过**任何**一帧事件 —— 它决定了失败时能不能降级重试：
  // 一帧都没收到说明请求根本没进流（越权/配额/未登录），重试是同样的结果。
  let gotEvent = false
  const mark = () => { gotEvent = true }
  try {
    await streamChat(payload, {
      // ⑤ `status` 是协议里唯一的"开始"信号：它一到达就说明连上了、后端开工了。
      // 消费方式是点亮 `phase`，让 Agent 侧在第一个节点跑完前显示占位
      // （analyze 要花一秒左右，那期间界面不该是一片空白）。
      status: (d) => {
        mark()
        slot.phase = d.phase
      },
      meta: (d) => {
        mark()
        // 引用在答案之前就画出来 —— 用户先看到"依据来自哪几份文档"
        Object.assign(slot.data, {
          sources: d.sources || [],
          hit: d.hit,
          // ⚠️ `answer_mode` 必须在这里接：它是「答案性质」标签的唯一来源。
          // 漏了这一步的后果很隐蔽 —— 标签只在"降级重试"和"翻历史"两条路上显示，
          // 而**正常流式回答（唯一入口）永远不显示**，没人会立刻发现。
          answer_mode: d.answer_mode,
          reranked: d.reranked,
          searched_kb_ids: d.searched_kb_ids,
          skipped_orphans: d.skipped_orphans,
          model: d.model,
        })
      },
      delta: (d) => {
        mark()
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
        mark()
        slot.phase = null // 已经有进度可看了，占位让位
        slot.data.steps.push(d)
        // 流式期间 meta 还没到，检索轮数先自己数着（done/meta 里的才是权威值）
        if (d.node === 'retrieve') slot.data.iterations = (slot.data.iterations || 0) + 1
      },
      done: (d) => {
        mark()
        slot.phase = null
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
        // 同上：done 里也带一份 answer_mode（meta 那一帧万一没到，这里是兜底）
        if (d.answer_mode) slot.data.answer_mode = d.answer_mode
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
      url: streamUrl(agentMode),
    })
    ok = true
  } catch (e) {
    flush()
    if (e?.name === 'AbortError') {
      // 用户主动停止。后端落库发生在 yield done 之前，停止的瞬间可能刚好已经落库，
      // 前端无从判断 —— 所以文案保持中性，不下"存没存"的结论。
      slot.stopped = true
      maybePersisted = true
    } else if (gotEvent || e?.transport) {
      // ④ 流真的开始了才谈降级 —— 见 `retryWithoutStream` 的说明。
      //    `e.transport` 是第二个入口：传输层就坏了（nginx 502/504）时一帧都没收到，
      //    但那恰恰是最该重试的场景（本项目 HTTP 恒 200，非 200 必然不是我们的信封）。
      const recovered = await retryWithoutStream(
        slot, payload, agentMode, controller.signal
      )
      if (recovered) {
        maybePersisted = true // 重试走非流式落库，会话必然已建
      } else if (slot.data.answer && e?.code === 'STREAM_TRUNCATED') {
        // 连接被掐断（反代空闲超时 / 网络抖动）：字节没送到，但**后端多半已经落完库**了 ——
        // 落库不看客户端还在不在。所以这里不能用"未存入历史"那句，那是流内 error 的口径。
        slot.cutoff = true
        maybePersisted = true
      } else if (slot.data.answer) {
        slot.interrupted = true // 流内 error：后端明确不落库，可以下这个结论
      } else {
        // ④ 降级也失败了：**必须把降级那次的原因带出来**。http.js 的拦截器会弹一个
        // 全局 toast，而这里显示的是另一句 —— 两处信息不一致时，用户会把 toast 当误触，
        // 并且因为看不到真实原因（很可能就是"配额到顶"）而反复重试。
        slot.error = true
        slot.content = slot.retryError
          ? `重新获取也失败了：${slot.retryError}`
          : e?.message || '请求失败，请稍后重试'
      }
    } else {
      // 一帧都没收到：请求根本没进流（越权 / 配额到顶 / 未登录 / 网络不通）。
      // 这种失败重试一次是同样的结果，直接显示后端那句话。
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

/**
 * ④ SSE 降级重试：流开始之后断了，用**非流式**端点再要一次整段答案。
 *
 * 为什么值得做：SSE 是长连接，放到 nginx 后面被 `proxy_read_timeout` 掐断是常态
 * （本地 Vite 的 120s 只是碰不到）。一旦断了，用户手里只有半截答案，而**非流式那条路
 * 照样能跑** —— 同一个后端、同一套检索与生成。有了它，SSE 出问题最多是"慢一点"，
 * 不再是"整个问答页废了"。
 *
 * 三条边界：
 *  · **只重试一次**。第二次再失败就照常报错，绝不循环（否则一次网络故障会放大成
 *    好几次付费调用）。
 *  · **只在流真的开始过之后**。一帧都没收到的失败（越权 / 配额到顶 / 未登录）
 *    重试一次是同样的结果，白花一次请求 —— 由调用方用 `gotEvent` 把关。
 *  · **用户主动停止不重试**（AbortError），那条路径在调用方就分流掉了。
 *
 * @returns {Promise<boolean>} 是否拿到了完整答案
 */
async function retryWithoutStream(slot, payload, agentMode, signal) {
  slot.retrying = true
  try {
    // ⚠️ signal 必须传下去：`ask/askAgent` 的 config 形参就是为它加的。
    // 不传的话，用户切走会话之后这次整段请求还会跑满 180s（http.js 的 timeout），
    // 并且再花掉一次付费调用 —— 而它的结果已经没人要了。
    // ⚠️ mock 模式下**非流式也必须走 mock**：否则"降级重试"会打到真后端，
    // 真调一次大模型、真写一轮库 —— 花着钱验着一个被污染的场景。
    // `baseURL: ''` 是为了绕开 axios 实例上的 `/api`，直接打 vite 的 `/__mock__/…`。
    const cfg = MOCK_MODE ? { signal, baseURL: '' } : { signal }
    const url = MOCK_MODE ? mockNonStreamUrl(agentMode) : undefined
    const body = agentMode
      ? await chatApi.askAgent(payload, cfg, url)
      : await chatApi.ask(payload, cfg, url)
    const d = body.data || {}
    Object.assign(slot.data, {
      answer: d.answer || '',
      sources: d.sources || [],
      hit: d.hit,
      reranked: d.reranked,
      answer_mode: d.answer_mode,
      iterations: d.iterations,
      searched_kb_ids: d.searched_kb_ids,
      skipped_orphans: d.skipped_orphans,
      model: d.model,
      usage: d.usage,
      finish_reason: d.finish_reason,
      elapsed_ms: d.elapsed_ms,
    })
    // Agent 的轨迹整段回来，直接覆盖（step 期间攒的是"过程视图"）
    if (agentMode && d.steps?.length) slot.data.steps = d.steps
    if (d.conversation_id && !signal.aborted) {
      currentConvId.value = d.conversation_id
    }
    slot.recovered = true
    return true
  } catch (e2) {
    // ④ 把降级失败的原因存下来交给调用方：它很可能就是"配额到顶"——
    // 降级那次走的是同一个 chat 限流器。不存的话界面只会显示原中断文案，
    // 用户会把它当网络问题反复重试。
    slot.retryError = e2?.message || '未知原因'
    return false // 降级也没救回来，交回调用方按原中断语义提示
  } finally {
    slot.retrying = false
  }
}

async function scrollToBottom() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

// ==================== 导出 ====================

/**
 * 当前对话的标题：列表里有就用列表的（后端按首个问题生成），
 * 还没落库的新对话就叫"新对话"。
 */
function conversationTitle() {
  return conversationTitleOf(currentConvId.value, conversations.value)
}

// 「拼 Markdown（含分页不完整警告）/ 时间戳 / 相似度百分数」搬去了
// `src/utils/exportMarkdown.js` —— 那边有测试专门守着 hasOlder 这类分支。

// ==================== 答案反馈（Bad Case 闭环）====================

const FEEDBACK_REASONS = ['答案错误', '与问题无关', '文不对题', '信息过时', '其他']
const feedbackPending = ref(null) // 正在填差评原因的那条消息
const fbReason = ref('')
const fbComment = ref('')
// el-dialog 的 v-model 要布尔值，而 feedbackPending 是个对象 ——
// 用 computed 派生，避免两套状态各自维护而不同步
const feedbackDialogOpen = computed({
  get: () => feedbackPending.value != null,
  set: (v) => { if (!v) feedbackPending.value = null },
})

async function sendFeedback(m, rating, reason = null, comment = null) {
  const before = m.myFeedback
  m.myFeedback = rating // 先本地点亮，失败再退回
  try {
    await feedbackApi.submit(m.id, { rating, reason, comment })
    ElMessage.success('感谢反馈')
  } catch {
    m.myFeedback = before
    /* 拦截器已提示 */
  }
}

function openDownFeedback(m) {
  // 已经点过"没用"再点一次 = 改成"有用"（改主意是常态，后端是 upsert）
  if (m.myFeedback === 'down') {
    sendFeedback(m, 'up')
    return
  }
  feedbackPending.value = m
  fbReason.value = FEEDBACK_REASONS[0]
  fbComment.value = ''
}

async function confirmDownFeedback() {
  const m = feedbackPending.value
  if (!m) return
  await sendFeedback(m, 'down', fbReason.value || null, fbComment.value.trim() || null)
  feedbackPending.value = null
}

function cancelDownFeedback() {
  feedbackPending.value = null
}

// ==================== 引用溯源 ====================

const sourceDetail = ref(null)
const sourceLoading = ref(false)
const sourceDialogOpen = computed({
  get: () => sourceDetail.value != null,
  set: (v) => { if (!v) sourceDetail.value = null },
})

/** 点引用编号 → 拉这块**完整**原文并弹窗。
 *  失败不外抛：溯源是辅助功能，查不到（比如引用的文档已被删）不该打断阅读。 */
async function openSource(s) {
  if (!s?.chunk_id) return
  sourceDetail.value = { loading: true, index: s.index, source: s }
  sourceLoading.value = true
  try {
    const body = await chunkApi.get(s.chunk_id)
    sourceDetail.value = { loading: false, index: s.index, source: s, data: body.data }
  } catch {
    sourceDetail.value = null // 拦截器已提示
  } finally {
    sourceLoading.value = false
  }
}

// ==================== 常见问题（FAQ 置顶）====================
//
// 只在**单选了一个知识库**时展示：FAQ 是挂在库上的，多选或全库检索时
// 无法回答"这些卡片属于哪个库"，硬凑只会误导。
const faqs = ref([])
const singleKbId = computed(() =>
  selectedKbs.value.length === 1 ? selectedKbs.value[0] : null
)

async function loadFaqs() {
  if (singleKbId.value == null) {
    faqs.value = []
    return
  }
  try {
    const body = await faqApi.list(singleKbId.value)
    faqs.value = body.data || []
  } catch {
    faqs.value = [] // 无权或库里没配，静默略过 —— 置顶区不该挡住正常问答
  }
}

watch(singleKbId, loadFaqs)

/** 点 FAQ 卡片 = 用这个问题直接提问（省掉一次打字）。 */
async function askFaq(f) {
  query.value = f.question
  await ask()
}

/** 导出当前对话为 Markdown 前的辅助：确认当前会话标题。 */
function exportConversation() {
  if (!messages.value.length) return
  const md = buildConversationMarkdown({
    title: conversationTitle(),
    mode: mode.value,
    messages: messages.value,
    hasOlder: convHasOlder.value,
    stamp: formatStamp(new Date()),
  })
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  // 文件名里去掉路径非法字符，否则 Windows 上会静默下载失败
  a.download = `${conversationTitle().replace(/[\\/:*?"<>|]/g, '_')}.md`
  a.href = url
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success('已导出 Markdown')
}

onMounted(async () => {
  await Promise.all([loadKbList(), loadConversations()])
  // FAQ 依赖知识库列表，等它回来再拉一次（首次渲染时 selectedKbs 还是空的）
  await loadFaqs()
})

// 离开页面时别让流还挂着：它占着一条连接和一个后端线程，done 回调还会写已卸载组件的状态
onBeforeUnmount(() => abortStream())
</script>

<template>
  <div class="chat-page">
    <!-- 左：会话列表 -->
    <aside class="side">
      <ConversationList
        :conversations="conversations"
        :current-id="currentConvId"
        :loading="convLoading"
        :has-more="convHasMore"
        @new="newConversation"
        @select="openConversation"
        @remove="removeConversation"
        @load-more="loadConversations({ append: true })"
      />
      <MemoryPanel />
    </aside>

    <!-- 右：对话区 -->
    <div class="main-col">
      <!-- dev-only 故障模拟工具条。
           ⚠️ 它必须是 `.main-col` 的子元素，**不能放在 `.chat-page` 下**：
           后者是 `display: flex` 的横向容器，直接子元素会和三列（会话列表 / 主列 /
           记忆列）横排争宽度 —— 主列被压扁后，模式选择、消息区、输入框全被挤出
           可见区，界面看起来"没有问答功能"。（这个错从加上它的那轮就存在，
           跑 build 和 curl 都发现不了，只有真看浏览器才会暴露。）
           `import.meta.env.DEV` 在 build 时被静态替换成 false，运行时不渲染。 -->
      <div v-if="isDev" class="mock-bar">
        <span class="mock-bar-label">SSE 故障模拟</span>
        <button
          v-for="b in MOCK_BUTTONS"
          :key="b.mode"
          class="mock-btn"
          :class="{ on: MOCK_MODE === b.mode }"
          @click="setMock(b.mode)"
        >{{ b.label }}</button>
        <button v-if="MOCK_MODE" class="mock-btn off" @click="setMock(null)">关闭</button>
        <span class="mock-bar-hint">
          当前：<b>{{ MOCK_MODE || '未启用（连真后端）' }}</b>
          ｜流式与非流式都会走 mock，不花钱、不写库
          ｜rag / agent 切换决定模拟哪条协议
        </span>
      </div>

      <el-card class="head-card">
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

          <!-- 导出当前对话为 Markdown。**只导界面上看得到的**：
               脱敏过的答案导出来就是脱敏后的，不会把无权看的原文写进文件。 -->
          <el-button
            size="default"
            :disabled="!messages.length"
            title="把当前对话（含引用来源）导出为 Markdown"
            @click="exportConversation"
          >导出对话</el-button>
        </div>
        <div class="mode-hint">{{ emptyHint }}</div>
      </el-card>

      <!-- 常见问题置顶。**只在单选了一个知识库时出现** —— FAQ 挂在库上，
           多选或全库检索时说不清这些卡片属于谁，硬凑只会误导。
           点卡片直接用这个问题提问，省掉一次打字，也绕开一次模型调用。 -->
      <el-card v-if="faqs.length" class="faq-card">
        <div class="faq-head">常见问题</div>
        <div class="faq-list">
          <button
            v-for="f in faqs"
            :key="f.id"
            class="faq-item"
            :title="f.answer"
            @click="askFaq(f)"
          >{{ f.question }}</button>
        </div>
      </el-card>

      <el-card class="body-card">
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
                  <!-- ⑤ 后端已开工、但第一个节点还没回来（Agent 的 analyze 要一秒上下）。
                       没有它，那段时间里这条消息是一片空白，用户不知道它到底动没动。 -->
                  <div v-if="m.streaming && m.phase && m.mode === 'agent'" class="stream-hint">
                    {{ PHASE_TEXT[m.phase] || m.phase }}
                  </div>
                  <!-- ④ 正在用非流式端点重取完整答案 -->
                  <div v-if="m.retrying" class="stream-hint">
                    连接中断，正在重新获取完整答案…
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
                  <!-- ④ 降级成功：答案是完整的，只是来路换了，交代一句 -->
                  <div v-else-if="m.recovered" class="stream-note">
                    流式连接中断过一次，这条答案是用普通模式补取的（完整，已存入历史）
                  </div>
                  <!-- 被 max_tokens 截断：与上面几条**独立**判断（可能叠加出现）。
                       判据来自 meta 里的 finish_reason，当场答的与翻出来的老消息同源。 -->
                  <div v-if="m.data.finish_reason === 'length'" class="stream-note">
                    答案达到长度上限被截断，后面的内容没生成完 —— 换个问法或让他分段回答
                  </div>

                  <!-- 元信息：接口不同、回放历史与实时回答的字段也略有差异，逐项判空 -->
                  <div class="meta">
                    <el-tag v-if="m.data.answer_mode" size="small" :type="answerModeTag(m.data.answer_mode).type">
                      {{ answerModeTag(m.data.answer_mode).text }}
                    </el-tag>
                    <!-- 脱敏消息：当时的口径全都取不回来了（meta 只剩 source），
                         不能说成"未使用知识库资料" —— 它其实用过资料，只是现在无权看了 -->
                    <el-tag v-if="m.data.redacted" size="small" type="warning">资料已无权访问</el-tag>
                    <!-- 文档清单（catalog）：答案来自系统元数据、没有可引用的正文 —— 
                         不能落进下面那个三元说成"有资料依据"（那是指文档正文） -->
                    <el-tag v-else-if="m.data.answer_mode === 'catalog'" size="small" type="info">系统文档清单</el-tag>
                    <!-- 流式中 meta 事件还没到，hit 还是 null。
                         这时不能落进下面那个三元 —— 会把"还没检索完"说成"未使用知识库资料"。 -->
                    <el-tag v-else-if="m.data.hit === null" size="small" type="info">检索中…</el-tag>
                    <el-tag v-else size="small" :type="m.data.hit ? 'success' : 'info'">
                      {{ m.data.hit ? '有资料依据' : '未使用知识库资料' }}
                    </el-tag>
                    <span v-if="m.mode === 'rag' && !m.data.redacted">
                      重排：{{ m.data.reranked ? '已重排' : '未重排' }}
                    </span>
                    <!-- 只在**真的检索过**时显示轮数（>0）：文档任务与寒暄的 iterations 是 0，
                         "检索轮数：0" 不是信息而是噪声；文档任务"取没取过数"看 sources 与命中标签。 -->
                    <span v-if="m.mode === 'agent' && m.data.iterations > 0">检索轮数：{{ m.data.iterations }}</span>
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

                  <!-- 答案反馈（Bad Case 闭环）。只出现在 assistant 消息上，
                       而且**流式生成中不显示** —— 让用户在答案还在往外吐时就打分，
                       打的是半截内容，数据没有意义。 -->
                  <div v-if="!m.streaming && m.id != null" class="feedback">
                    <el-button
                      size="small"
                      text
                      :type="m.myFeedback === 'up' ? 'success' : 'info'"
                      @click="sendFeedback(m, 'up')"
                    >有用</el-button>
                    <el-button
                      size="small"
                      text
                      :type="m.myFeedback === 'down' ? 'danger' : 'info'"
                      @click="openDownFeedback(m)"
                    >没用</el-button>
                    <span v-if="m.myFeedback" class="feedback-done">已反馈</span>
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
                        <div
                          v-for="s in m.data.sources"
                          :key="s.chunk_id"
                          class="source"
                        >
                          <!-- 引用编号可点击 → 弹窗看**完整**原文（溯源）。
                               列表里的 snippet 是截断过的，点进去才是全貌。 -->
                          <div class="source-head">
                            <el-tag
                              size="small"
                              type="primary"
                              class="cite-tag"
                              title="点击查看完整原文"
                              @click="openSource(s)"
                            >[{{ s.index }}]</el-tag>
                            <span class="doc-name">{{ s.document_name }}</span>
                            <span v-if="s.page_number != null" class="page">第 {{ s.page_number }} 页</span>
                            <span class="score">{{ sourceScoreText(s.score, s.retrieval_path) }}</span>
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

      <!-- 差评原因弹窗。只在点「没用」时弹 —— 好评不需要追问原因，
           让用户为"点个赞"再过一道弹窗会直接劝退。 -->
      <el-dialog
        v-model="feedbackDialogOpen"
        title="这条回答有什么问题？"
        width="420px"
        :close-on-click-modal="false"
      >
        <el-radio-group v-model="fbReason" class="fb-reasons">
          <el-radio v-for="r in FEEDBACK_REASONS" :key="r" :value="r">{{ r }}</el-radio>
        </el-radio-group>
        <el-input
          v-model="fbComment"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="补充一下具体情况（可选），比如正确答案应该是什么"
        />
        <template #footer>
          <el-button @click="cancelDownFeedback">取消</el-button>
          <el-button type="primary" @click="confirmDownFeedback">提交</el-button>
        </template>
      </el-dialog>

      <!-- 引用溯源弹窗：点答案里的 [n] 看这段引用的**完整**原文。
           列表里显示的是 snippet（截断过的），这里才是全貌 ——
           用户点引用就是想看"被截掉的那部分"。 -->
      <el-dialog
        v-model="sourceDialogOpen"
        :title="sourceDetail?.data
          ? `引用 [${sourceDetail.index}] 原文 · ${sourceDetail.data.document_name}`
          : `引用 [${sourceDetail?.index}] `
        "
        width="640px"
      >
        <div v-if="sourceLoading" class="src-loading">加载中…</div>
        <template v-else-if="sourceDetail?.data">
          <div class="src-meta">
            <span>《{{ sourceDetail.data.document_name }}》</span>
            <span v-if="sourceDetail.data.page_number != null">第 {{ sourceDetail.data.page_number }} 页</span>
            <span>第 {{ sourceDetail.data.chunk_index + 1 }} 块</span>
            <span>{{ sourceDetail.data.char_count }} 字</span>
            <span class="src-score">{{ sourceScoreText(sourceDetail.source.score, sourceDetail.source.retrieval_path) }}</span>
          </div>
          <pre class="src-body">{{ sourceDetail.data.content }}</pre>
        </template>
        <template #footer>
          <el-button @click="sourceDetail = null">关闭</el-button>
        </template>
      </el-dialog>

      <el-card class="input-card">
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
  /* 与 .main-col 同一个坑：flex 子项 min-height 默认 auto（=内容高度）→ 拒绝收缩。
     侧边栏内容一多就把 .chat-page 顶出去，el-main 的滚动条又出来了 */
  min-height: 0;
}

/* ---- 右侧对话区 ---- */
.main-col {
  flex: 1;
  min-width: 0;
  /* flex 子项的 min-height 默认是 auto（= 内容高度）→ **拒绝被压缩**。
     不归零的话，三张卡片内容一多就顶破 .chat-page 的 100%，
     而 el-main 自带 overflow:auto → 页面级出现第二条滚动条 */
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
/* 三张卡片都要归零：body-card 靠 flex:1 吸收剩余高度，
   另外两张若拒绝收缩，总量就把 .chat-page 顶出去（外层滚动条就是这么来的） */
.head-card,
.input-card,
.body-card {
  min-height: 0;
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
  font-size: var(--el-font-size-extra-small);
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
  /* EP 给 `.el-card__body` 默认带 `overflow: auto`（见 theme-chalk/el-card.css）。
     不关掉它，卡片体自己就是一条滚动条，和下面 `.scroller` 那条并排出现 ——
     看上去像"页面右边有两根滑块"。滚动只该由 .scroller 负责。 */
  overflow: hidden;
}
.scroller {
  height: 100%;
  overflow-y: auto;
  padding: 16px;
  /* 全站没有 box-sizing 重置（EP 的 base.css 里 border-box 出现 0 次），而 content-box
     下 `height:100%` + 上下 padding 的实际占位是 100% + 32px —— 那 32px 既会把父级顶出
     可滚内容（喂给上面那条多余的滚动条），也会把消息区底部裁掉。 */
  box-sizing: border-box;
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
  font-size: var(--el-font-size-extra-small);
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
  color: var(--el-color-white);
  white-space: pre-wrap;
  word-break: break-word;
}
.agent-bubble {
  background: var(--el-bg-color);
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
/* dev-only 故障模拟工具条（build 时整条不渲染，见 isDev 的说明）。
   在 `.main-col` 的竖向 flex 里，`flex: none` 防止它被压高度。 */
.mock-bar {
  flex: none;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 10px;
  border: 1px dashed var(--el-color-warning);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  font-size: var(--el-font-size-extra-small);
}
.mock-bar-label {
  font-weight: 600;
  color: var(--el-color-warning);
}
.mock-btn {
  padding: 2px 8px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  cursor: pointer;
  font-size: var(--el-font-size-extra-small);
}
.mock-btn.on {
  border-color: var(--el-color-warning);
  color: var(--el-color-warning);
  font-weight: 600;
}
.mock-btn.off {
  color: var(--el-text-color-secondary);
}
.mock-bar-hint {
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}

/* 流中途断了：正文保留，只在下面加一行说明（并说清没进历史）。 */
.stream-note {
  margin-top: 6px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-color-warning);
}
/* 正在等 / 正在重取：不是错误，只是"还在动"的说明，用中性色跟 stream-note 区分。 */
.stream-hint {
  margin-top: 6px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
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
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}
.trace {
  margin-top: 8px;
}
.trace-detail {
  color: var(--el-text-color-secondary);
  font-size: var(--el-font-size-extra-small);
}
.sources {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.sources-title {
  font-size: var(--el-font-size-extra-small);
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
  font-size: var(--el-font-size-extra-small);
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
  font-size: var(--el-font-size-extra-small);
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
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}

/* ---- 答案反馈 ---- */
.feedback {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
}

.feedback-done {
  margin-left: 4px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}

.fb-reasons {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

/* ---- 常见问题置顶 ---- */
.faq-card :deep(.el-card__body) {
  padding: 10px 16px;
}

.faq-head {
  margin-bottom: 8px;
  font-size: var(--el-font-size-extra-small);
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.faq-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 用原生 button 而不是 el-tag：它要能点击触发提问，语义上就是个按钮 */
.faq-item {
  max-width: 240px;
  padding: 5px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 14px;
  background: var(--el-bg-color);
  color: var(--el-text-color-regular);
  font-size: var(--el-font-size-extra-small);
  line-height: 1.6;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.faq-item:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

/* ---- 引用溯源 ---- */
.cite-tag {
  cursor: pointer;
}

.src-loading {
  padding: 20px 0;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.src-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 10px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}

.src-score {
  color: var(--el-color-primary);
}

/* 原文用 <pre>：制度文档里的换行和缩进本身就是信息，
   换成普通 div 会把它们吃掉。 */
.src-body {
  max-height: 50vh;
  overflow-y: auto;
  margin: 0;
  padding: 12px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
