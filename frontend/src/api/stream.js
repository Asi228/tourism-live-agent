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

/**
 * 发一次流式问答。
 *
 * @param {object} payload 与 /api/chat 相同：`{ query, kb_ids?, conversation_id? }`
 * @param {{meta?:Function, delta?:Function, done?:Function}} handlers 事件回调
 * @param {{signal?: AbortSignal}} [options] `signal` 用于中止（新建会话/卸载时），
 *   中止后 fetch/read 会以 AbortError reject，由调用方区分处理
 * @returns {Promise<void>} 流正常走完 resolve；准备阶段失败 / 网络错误 / 流内 error /
 *   流被掐断（没等到 done 就关闭）都 reject
 */
export async function streamChat(payload, handlers = {}, { signal } = {}) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(payload),
    signal,
  })

  const contentType = resp.headers.get('content-type') || ''

  // ① 准备阶段就失败了 → 普通信封，不是流
  if (!contentType.includes('text/event-stream')) {
    const body = await resp.json().catch(() => null)
    const code = body?.code
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
