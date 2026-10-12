/**
 * dev-only：SSE / 非流式 故障模拟中间件（vite `configureServer` 挂载）。
 *
 * 为什么需要它：流式已经是问答在界面上的**唯一入口**（非流式已从 UI 移除），
 * 而降级重试这条兜底路径**零自动化覆盖**（前端没有测试栈）。也就是说
 * "流式坏 + 降级也失效"这个组合，在测试里一条都发现不了 —— 而这个中间件把
 * 断流、502、半截答案被覆盖、三种中断提示、降级也失败，全部变成一次点击可复现。
 *
 * 刻意做成 vite dev server 的中间件，而不是给后端加 `break_after` 调试参数：
 *   · 不碰生产代码路径（后端永远看不到这个开关）；
 *   · 不加调试参数（少一个"生产上忘了它是开的"的状态）；
 *   · 零新依赖。
 *
 * ⚠️ **流式和非流式都拦**：只拦流式的话，mock 断流之后的"降级重试"会打到
 * **真后端**（真调大模型、真写库），既花钱又制造脏数据，而且模拟的就不是
 * 降级本身了。所以 `/chat` 也一起 mock 掉。
 *
 * 路径（注意 connect 会把 `/__mock__` 前缀 strip 掉，见中间件里的注释）：
 *   /chat/stream        流式 RAG
 *   /agent/chat/stream  流式 Agent
 *   /chat               非流式 RAG（降级重试的落点）
 *   /agent/chat         非流式 Agent
 */

/** SSE 响应头 —— 与后端 `app/api/sse.py` 逐字一致（少一个头就不是同一条路）。 */
const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache',
  'Connection': 'keep-alive',
  'X-Accel-Buffering': 'no',
}

/** 拼一帧。格式必须与后端逐字一致。 */
function frame(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

// ==================== 帧内容 ====================

const RAG_META = {
  hit: true, sources: [], searched_kb_ids: [1], reranked: false,
  skipped_orphans: 0, model: 'mock-model', conversation_id: null,
}

const RAG_DELTAS = [frame('delta', { text: '这是' }), frame('delta', { text: '模拟出来的答案。' })]

const RAG_DONE = {
  answer: '这是模拟出来的答案。',
  usage: { input_tokens: 1, output_tokens: 2, total_tokens: 42 },
  finish_reason: 'stop', conversation_id: null, saved: true,
}

const AGENT_META = {
  hit: true, sources: [], answer_mode: 'grounded', iterations: 1,
  searched_kb_ids: [1], model: 'mock-model', conversation_id: null,
}

const AGENT_STEPS = [
  frame('step', { node: 'analyze', ms: 12 }),
  frame('step', { node: 'retrieve', ms: 30 }),
  frame('step', { node: 'evaluate', ms: 8, mode: '依据资料' }),
]

const AGENT_DONE = {
  answer: '这是 Agent 模拟出来的答案。',
  usage: { input_tokens: 1, output_tokens: 2, total_tokens: 42 },
  answer_mode: 'grounded', iterations: 1,
  steps: [
    { node: 'analyze', ms: 12 }, { node: 'retrieve', ms: 30 },
    { node: 'evaluate', ms: 8 }, { node: 'generate', ms: 20, mode: '依据资料', adopted: 1 },
  ],
  elapsed_ms: 70, conversation_id: null, saved: true,
}

const ERROR_FRAME = frame('error', { code: 50005, message: '大模型调用失败，请稍后重试' })

/**
 * 协议类模式：控制"流怎么死"。
 *   normal  完整跑完（基准）
 *   break   吐 2 帧后直接断开连接（反代 read_timeout 掐断）→ 前端 STREAM_TRUNCATED
 *   hang    首帧之前挂 3 秒（上游排队，看 status 前的界面）
 *   http502 返回 502 + HTML（反代本身坏了）→ 前端 transport 判据
 *   error   吐到一半发 error 帧 → "流内 error 明确不落库"那条分支
 *
 * ⚠️ break / error 的下标**按各自的帧序列定**：Agent 的正文只在 done 里给，
 * 照抄 RAG 的下标会让 agent 的 break 停在 status+1 个 step —— 能触发，但永远
 * 不会出现"半截正文"，按钮文案就对不上了。
 */
const PROTOCOLS = {
  normal: {},
  break: { ragAfter: 2, agentAfter: 2 },
  hang: { hangMs: 3000 },
  http502: {},
  error: { ragAfter: 3, agentAfter: 3 },
}

/**
 * 定制类模式：流正常走完，只改 `done` 的字段 —— 覆盖那三种前端提示分支。
 *   unsaved    落库失败 → done.saved=false（前端"这次没能存入历史"）
 *   truncated  被 max_tokens 截断 → finish_reason='length'（前端截断提示）
 *   withconv   带一个非 null 的会话 id → 回写 currentConvId + abort 守卫那条竞态
 */
const DONE_OVERRIDES = {
  unsaved: {
    saved: false,
    answer: '答案完整，但这一轮没能存入历史。',
  },
  truncated: {
    finish_reason: 'length',
    answer: '答案写到这里就被长度上限截断了，后面的内容没有生成完 ——',
  },
  withconv: {
    conversation_id: 999,
  },
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function buildFrames(agent, mode) {
  const override = DONE_OVERRIDES[mode]
  if (agent) {
    const done = { ...AGENT_DONE, ...(override || {}) }
    return [frame('status', { phase: 'start' }), ...AGENT_STEPS,
            frame('meta', AGENT_META), frame('done', done)]
  }
  const done = { ...RAG_DONE, ...(override || {}) }
  // truncated 模式下正文也跟着短一截，界面才看得出"被截断了"
  const deltas = override === DONE_OVERRIDES.truncated
    ? [frame('delta', { text: '答案写到这里就被长度上限截断了，' })]
    : RAG_DELTAS
  return [frame('status', { phase: 'start' }), frame('meta', RAG_META),
          ...deltas, frame('done', done)]
}

/** 非流式信封：字段形状照 `/api/chat` 的响应，`code` 必须是数字（http.js 靠它放行）。
 *
 * 注意 `rerank_skipped_reason` / `conversation_id` 用 **null** 而不是省略：
 * 前端对 `searched_kb_ids` 是 `=== null`（管理员不受限）还是 `=== []`（没检索）
 * 是两种语义，mock 必须能表达出来。
 */
function envelope(agent, mode) {
  const override = DONE_OVERRIDES[mode] || {}
  const base = {
    query: '模拟问题',
    answer: '这是模拟出来的答案。',
    hit: true,
    sources: [],
    searched_kb_ids: [1],
    answer_mode: 'grounded',
    model: 'mock-model',
    usage: { input_tokens: 1, output_tokens: 2, total_tokens: 42 },
    finish_reason: 'stop',
    reranked: false,
    rerank_skipped_reason: null,
    skipped_orphans: 0,
    conversation_id: null,
  }
  if (agent) {
    base.iterations = 1
    base.steps = AGENT_DONE.steps
    base.elapsed_ms = 70
  }
  return { code: 0, message: 'ok', data: { ...base, ...override } }
}

/**
 * connect 风格的中间件。
 *
 * ⚠️ `req.url` 里**已经没有 `/__mock__` 前缀**了：`server.middlewares.use(path, fn)`
 * 会把匹配到的前缀 strip 掉再交给 fn。所以这里看到的是 `/chat/stream?mode=…`
 * 或 `/agent/chat?fallback=…` —— 判断 agent 要看 `/agent/` 开头
 * （第一版写成 `/__mock__/agent/`，结果 agent 请求静默走了 RAG 协议）。
 */
export function mockSseMiddleware(req, res, next) {
  const url = new URL(req.url, 'http://localhost')
  const path = url.pathname
  const agent = path.startsWith('/agent/')
  const mode = url.searchParams.get('mode') || 'normal'
  const fallback = url.searchParams.get('fallback')

  // ---- 非流式：降级重试的落点。也 mock 掉，否则降级会打真后端 ----
  if (path === '/chat' || path === '/agent/chat') {
    if (fallback === '502') {
      // 降级也失败：反代坏到底。用来验"降级失败原因被带出来"那条分支
      res.statusCode = 502
      res.setHeader('Content-Type', 'text/html')
      res.end('<html><head><title>502 Bad Gateway</title></head>'
        + '<body><center><h1>502 Bad Gateway</h1></center></body></html>')
      return
    }
    if (fallback === 'quota') {
      // 降级失败最常见的原因：配额到顶（走的是同一个 chat 限流器）
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ code: 40900, message: '提问次数已达上限，请稍后再试' }))
      return
    }
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify(envelope(agent, mode)))
    return
  }

  // ---- 流式 ----
  // ⚠️ 模式分两类：协议类（控制"流怎么死"，在 PROTOCOLS）与定制类（流正常走完、
  // 只改 done 字段，在 DONE_OVERRIDES）。校验要**两张表都认** —— 第一版只查了
  // PROTOCOLS，结果 unsaved / truncated / withconv 全被 400 挡掉（而错误信息里
  // 又列着它们，属于"说自己支持却不放行"）。
  const spec = PROTOCOLS[mode]
  const override = DONE_OVERRIDES[mode]
  if (!spec && !override) {
    res.statusCode = 400
    res.end(`未知 mock 模式：${mode}（可用：${Object.keys(PROTOCOLS).join(' / ')}`
      + ` / ${Object.keys(DONE_OVERRIDES).join(' / ')}）`)
    return
  }
  const protocol = spec || {}

  if (mode === 'http502') {
    res.statusCode = 502
    res.setHeader('Content-Type', 'text/html')
    res.end('<html><head><title>502 Bad Gateway</title></head>'
      + '<body><center><h1>502 Bad Gateway</h1></center></body></html>')
    return
  }

  const frames = buildFrames(agent, mode)
  const breakAfter = agent ? protocol.agentAfter : protocol.ragAfter
  const errorAfter = agent ? protocol.agentAfter : protocol.ragAfter

  res.writeHead(200, SSE_HEADERS)

  void (async () => {
    if (protocol.hangMs) await sleep(protocol.hangMs)
    for (let i = 0; i < frames.length; i++) {
      res.write(frames[i])
      await sleep(30) // 逐帧，肉眼能看出"流"的感觉
      if (errorAfter && i + 1 === errorAfter) {
        res.write(ERROR_FRAME)
        res.end()
        return
      }
      if (breakAfter && i + 1 === breakAfter) {
        // 直接掐断 socket：不发 done、不发 error，和反代超时断开一模一样
        res.destroy()
        return
      }
    }
    res.end()
  })()
}

/** vite 插件形态 —— `configureServer` 里 `server.middlewares.use` 挂上去。 */
export function mockSsePlugin() {
  return {
    name: 'mock-sse',
    configureServer(server) {
      // 直接 use（而不是钩子 return 函数）→ 装在 vite 内部中间件**之前**，
      // 不会被 `/api` 的 proxy 抢先匹配走。
      server.middlewares.use('/__mock__', mockSseMiddleware)
    },
  }
}
