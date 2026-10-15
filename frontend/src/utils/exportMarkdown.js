/**
 * 把一次对话拼成可下载的 Markdown。
 *
 * 从 Chat.vue 抽出来的理由：
 *  1. 它是**纯逻辑**（时间由调用方传入、消息由参数传入）—— 抽出来就能被
 *     `node --test` 直接跑，不用挂载组件、不用 jsdom；
 *  2. Chat.vue 已经 1000+ 行，这段本来就是完全独立的一块。
 *
 * 🔴 口径（别改）：**只导出界面上看得到的内容** —— 脱敏过的答案导出来
 * 就是脱敏后的，不会把用户已无权查看的原文写进文件（回放脱敏那一层天然继承）。
 */

/**
 * 时间戳 → `'2026-10-13 23:39'`。
 *
 * 做成"接收 Date"的纯函数是为了测试能传固定时间 ——
 * `new Date()` 藏在函数里面的话，输出里就有一段永远对不上的字符串。
 */
export function formatStamp(d) {
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + ` ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * @param {object}  p
 * @param {string}  p.title    会话标题
 * @param {string}  p.mode     'rag' | 'agent'（当前页面模式）
 * @param {Array}   p.messages 已加载的消息（**只有已加载的** —— 见下方 hasOlder）
 * @param {boolean} p.hasOlder 这段会话是否还有更早的消息没加载
 * @param {string}  p.stamp    formatStamp() 的结果
 */
export function buildConversationMarkdown({ title, mode, messages, hasOlder, stamp }) {
  const modeText = mode === 'agent' ? 'Agent 多步' : '纯 RAG'
  const out = [
    `# ${title}`,
    '',
    `> 导出于 ${stamp} ｜ 模式：${modeText} ｜ ${messages.length} 条消息`,
  ]
  // ⚠️ **导出的只是"已经加载出来的那些消息"。**
  // 会话详情是分页的（默认只给最新一页），更早的要点「加载更早的消息」才拉得到 ——
  // 不写这句提示，长会话的导出文件会**静默缺掉前半段**，而用户以为导的是全部。
  if (hasOlder) {
    out.push(
      '>',
      '> ⚠️ **本文件不完整**：这段会话更早的消息尚未加载，未包含在导出里。'
        + '如需完整内容，请先在界面上点「加载更早的消息」，把前面的部分翻出来后再导出。',
    )
  }
  out.push('')
  for (const m of messages) {
    out.push('---', '')
    if (m.role === 'user') {
      out.push(`**问：** ${m.content}`, '')
      continue
    }
    const answer = m.error ? (m.content || '（获取失败）') : (m.data?.answer || '')
    out.push(`**答：** ${answer}`, '')
    // 这一轮"不完整"的原因要一起带上 —— 否则导出件看上去像完整答案，
    // 日后回看会误以为当时就答全了（口径与界面上的提示条一致）。
    const note = m.interrupted ? '生成中断，本次未存入历史'
      : m.stopped ? '已停止生成（可能未存入历史）'
        : m.cutoff ? '连接中断，回答可能不完整（已生成部分多半已存进历史）'
          : m.unsaved ? '答案已完整返回，但这次没能存入历史'
            : m.recovered ? '流式连接中断过一次，这条答案是用普通模式补取的'
              : m.data?.finish_reason === 'length' ? '答案达到长度上限被截断'
                : ''
    if (note) out.push(`> ⚠️ ${note}`, '')
    const sources = m.data?.sources || []
    if (sources.length) {
      out.push('**引用来源：**', '')
      for (const s of sources) {
        const page = s.page_number != null ? `第 ${s.page_number} 页` : '无页码'
        out.push(
          `- [${s.index}] 《${s.document_name}》${page}，${sourceScoreText(s.score, s.retrieval_path)}`
        )
        if (s.snippet) out.push(`  > ${s.snippet}`)
      }
      out.push('')
    }
  }
  return out.join('\n')
}

/** 相似度/rerank 分 → 展示用百分数（0~100 整数）。 */
export function scorePercent(score) {
  // rerank 分数与余弦相似度都大致落在 0~1，展示成百分比更直观；
  // 负分（理论上向量余弦可以为负）夹到 0，避免进度条乱跳
  const v = Math.max(0, Math.min(1, Number(score) || 0))
  return Math.round(v * 100)
}

/**
 * 引用来源的「相关性」文案，界面与导出共用。
 *
 * 检索命中 → `相似度 87%`；**文档级命中（score 为 null）→ `整篇文档`** ——
 * Agent 的文档任务（总结/对比）取的是整篇文档，没有"相似度"这个概念。
 * 直接走 scorePercent 会显示"相似度 0%"（Number(null)=0），那是误导：看起来像不相关。
 *
 * **仅关键词路命中（retrieval_path === 'keyword'）→ `关键词匹配`** ——
 * 那个分数是词项覆盖率（命中词数 / 提词数），和余弦相似度同落 0~1 但语义不同，
 * 混着叫"相似度"是假精度（评审 2026-10-15 指出；来源由后端 retrieval_path 标注）。
 */
export function sourceScoreText(score, retrievalPath) {
  if (score == null) return '整篇文档'
  if (retrievalPath === 'keyword') return '关键词匹配'
  return `相似度 ${scorePercent(score)}%`
}
