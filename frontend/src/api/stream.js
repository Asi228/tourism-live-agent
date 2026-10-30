/**
 * SSE 流式问答 —— **刻意不走 axios**。
 *
 * `http.js` 的拦截器整条建立在"响应体是 `{code, message, data}` 信封"之上，
 * 而 SSE 的响应体是一帧帧事件 —— 会被它当成"这个响应没有 code"的异常情况。
 * 所以这里用原生 `fetch` + `ReadableStream`，同时**把那两件拦截器做过的事补回来**：
 * token 过期时清凭证跳登录、其余错误把后端那句话弹出来。
 * （不补的话，会出现"登录过期了但界面不跳登录"这种很难归因的问题。）
 *
 * ⚠️ **这个接口不是"总是返回流"**：鉴权、限流、越权这些失败发生在响应开始**之前**，
 * 后端返回的是普通 JSON 信封（HTTP 仍是 200）。所以必须先看 `content-type` 分流 ——
 * 这也是为什么不能想当然地拿 `EventSource`：它只支持 GET、也带不了 Authorization 头。
 */

import { getToken, setToken } from './http'
import { apiUrl } from './url'

/**
 * 发一次流式问答。
 *
 * @param {object} payload 与 /api/chat 相同：`{ query, kb_ids?, conversation_id? }`
 * @param {{meta?:Function, delta?:Function, done?:Function, step?:Function}} handlers 事件回调
 * @param {{signal?: AbortSignal, url?: string}} [options]
 *   `signal` 用于中止（新建会话/卸载时），中止后 fetch/read 会以 AbortError reject，
 *   由调用方区分处理；`url` 让同一份解析逻辑服务 RAG 与 Agent 两条路 ——
 *   帧格式、分流、收尾判定必须只有一份实现。
 * @returns {Promise<void>} 流正常走完 resolve；准备阶段失败 / 网络错误 / 流内 error /
 *   流被掐断（没等到 done 就关闭）都 reject
 */
export async function streamChat(payload, handlers = {}, { signal, url } = {}) {
  const resp = await fetch(apiUrl(url || '/chat/stream'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(payload),
    signal,
  })

  const contentType = resp.headers.get('content-type') || ''

  // ① 传输层坏了：**本项目 HTTP 恒 200，真错在 body.code** —— 所以非 200 按定义
  //    就不是我们的信封，必然是中间层出的问题（nginx 502/504、网关超时、连接被重置）。
  //    而这恰恰是 SSE 最常见的死法，也正是前端要做降级重试的场景；
  //    若把它并进下面那个"信封解析失败"分支，`gotEvent` 会是 false，重试规则会
  //    把它当成"越权/配额那种重试没用的失败"—— 降级就永远等不到它要救的场景。
  //    所以这里单独抛一个带 `transport: true` 的错误，作为"可重试"的第二个入口。
  if (!resp.ok) {
    const err = new Error(`服务暂时不可用（HTTP ${resp.status}），正在重新获取`)
    err.transport = true
    throw err
  }

  // ② 不是流 → 普通信封。**这里有两种截然不同的东西，必须按 code 分开**：
  //     · code === 0  → **成功**：FAQ 命中时后端直接给标准答案（答案是现成的，
  //                     没有"逐字生成"这个过程）。这种情况要**当成一次正常问答收尾**，
  //                     复用 meta/delta/done 三个回调，调用方一行都不用改。
  //     · 其余 code   → 失败（越权 40400、配额 40900、限流 42900…），照旧抛出。
  //     ⚠️ 顺序不能反：先判成功，再当失败 —— 否则 FAQ 命中会被当成"请求失败"弹出来。
  if (!contentType.includes('text/event-stream')) {
    const body = await resp.json().catch(() => null)
    const code = body?.code

    if (code === 0 && body?.data) {
      const d = body.data
      // 与 SSE 路径同序：先 meta（引用/标签），再 delta（正文），最后 done。
      handlers.meta?.(d)
      handlers.delta?.({ text: d.answer })
      handlers.done?.({
        answer: d.answer,
        usage: null,
        finish_reason: null,
        conversation_id: d.conversation_id,
        saved: true,
      })
      return
    }

    if (code === 40100) {
      setToken('')
      if (window.location.hash !== '#/login') window.location.hash = '#/login'
    }
    throw new Error(body?.message || `请求失败（code=${code ?? resp.status}）`)
  }

  // ② 正常流：按空行切帧
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  // done 收到了吗？流"正常关闭"却没等到 done，说明连接是被掐断的
  // （反代按空闲超时断开、网络抖动），这时候既没有 done 也没有 error ——
  // 若不当回事，调用方会把半截答案当成完整回答。
  let gotDone = false

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const ev = parseFrame(frame)
      if (!ev) continue

      if (ev.event === 'error') {
        // 服务端在流中途挂了 —— 响应头早就发出去了，它也只能用事件报错
        await reader.cancel().catch(() => {})
        const err = new Error(ev.data?.message || '生成失败')
        err.code = ev.data?.code
        throw err
      }
      if (ev.event === 'done') gotDone = true
      handlers[ev.event]?.(ev.data)
    }
  }

  if (!gotDone) {
    const err = new Error('连接中断，回答可能不完整')
    err.code = 'STREAM_TRUNCATED'
    throw err
  }
}

/** 解析一帧：`event: xxx` 行 + 可能多行 `data:`。解析不出来就返回 null（忽略这帧）。 */
function parseFrame(frame) {
  let name = 'message'
  const dataLines = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!dataLines.length) return null
  try {
    return { event: name, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return null
  }
}
