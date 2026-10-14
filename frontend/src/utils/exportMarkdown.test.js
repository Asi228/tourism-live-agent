/**
 * 导出 Markdown 的守卫（`node --test`，零依赖 —— 见 package.json 的 test 脚本）。
 *
 * 这块修过一次**真缺陷**：会话详情分页加载，而导出读的是"已加载的消息" ——
 * 长会话的导出文件会**静默缺掉前半段**，用户以为导的是全部。
 * 它现在做成了纯函数（时间由参数传入），所以"hasOlder 时必须写明不完整"
 * 这类分支第一次变得可测。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildConversationMarkdown, formatStamp, scorePercent, sourceScoreText } from './exportMarkdown.js'

const STAMP = '2026-10-13 23:39'

function build(messages, opts = {}) {
  return buildConversationMarkdown({
    title: '测试会话',
    mode: 'rag',
    messages,
    hasOlder: false,
    stamp: STAMP,
    ...opts,
  })
}

test('头部：标题 / 导出时间 / 模式 / 条数', () => {
  const md = build([])
  assert.ok(md.includes('# 测试会话'))
  assert.ok(md.includes(`> 导出于 ${STAMP}`))
  assert.ok(md.includes('模式：纯 RAG'))
  assert.ok(md.includes('0 条消息'))
  assert.ok(build([], { mode: 'agent' }).includes('模式：Agent 多步'))
})

test('🔴 hasOlder=true 必须写明「本文件不完整」（导出静默缺前半段的那条修复）', () => {
  const partial = build([{ id: 1, role: 'user', content: '问题' }], { hasOlder: true })
  assert.ok(partial.includes('本文件不完整'), '分页未加载时必须给出警告')
  assert.ok(partial.includes('加载更早的消息'))

  const full = build([{ id: 1, role: 'user', content: '问题' }], { hasOlder: false })
  assert.ok(!full.includes('本文件不完整'), 'hasOlder=false 时不该出现警告')
})

test('问答两条腿：user 出「问」、assistant 出「答」', () => {
  const md = build([
    { id: 1, role: 'user', content: '年假几天？' },
    { id: 2, role: 'assistant', data: { answer: '按工龄 5 / 10 / 15 天。' } },
  ])
  assert.ok(md.includes('**问：** 年假几天？'))
  assert.ok(md.includes('**答：** 按工龄 5 / 10 / 15 天。'))
})

test('「这一轮不完整」的六种原因全部带上（与界面提示条同一口径）', () => {
  const cases = [
    [{ interrupted: true }, '生成中断'],
    [{ stopped: true }, '已停止生成'],
    [{ cutoff: true }, '连接中断'],
    [{ unsaved: true }, '没能存入历史'],
    [{ recovered: true }, '普通模式补取'],
    [{ data: { answer: 'x', finish_reason: 'length' } }, '长度上限'],
  ]
  for (const [extra, expect] of cases) {
    const md = build([{ id: 2, role: 'assistant', data: { answer: 'x' }, ...extra }])
    assert.ok(md.includes(expect), `缺少「${expect}」提示：\n${md}`)
  }
})

test('引用来源：编号 / 文档名 / 页码 / 相似度 + 片段缩进；无页码给「无页码」', () => {
  const md = build([
    {
      id: 2,
      role: 'assistant',
      data: {
        answer: '答案',
        sources: [
          { index: 1, document_name: '考勤制度.md', page_number: 3, score: 0.876, snippet: '片段一' },
          { index: 2, document_name: '无页码文档.md', page_number: null, score: 0.5, snippet: '' },
        ],
      },
    },
  ])
  assert.ok(md.includes('- [1] 《考勤制度.md》第 3 页，相似度 88%'))
  assert.ok(md.includes('- [2] 《无页码文档.md》无页码，相似度 50%'))
  // snippet 只在非空时产出缩进引用行（空 snippet 不许留一行空引用）
  assert.equal((md.match(/^ {2}> /gm) || []).length, 1)
})

test('脱敏后的消息照原样导出（只导界面看得到的，不复活原文、不出现引用段）', () => {
  // 回放脱敏后 assistant 的 content 是后端给的占位串、sources 为空 ——
  // 导出件必须与界面所见一致
  const REDACTED = '（原回答已收起）' // 具体文案由后端决定，这里只验"占位串被照抄"
  const md = build([
    { id: 2, role: 'assistant', data: { answer: REDACTED, sources: [], redacted: true } },
  ])
  assert.ok(md.includes(`**答：** ${REDACTED}`))
  assert.ok(!md.includes('引用来源'), 'sources 为空时不该出现引用段')
})

test('error 消息：有 content 用 content，空 content 给「（获取失败）」', () => {
  assert.ok(
    build([{ id: 2, role: 'assistant', error: true, content: '半截答案' }])
      .includes('半截答案')
  )
  assert.ok(
    build([{ id: 3, role: 'assistant', error: true, content: '' }])
      .includes('（获取失败）')
  )
})

test('formatStamp：补零、只到分钟、本地时区按 Date 字段直取', () => {
  assert.equal(formatStamp(new Date(2026, 0, 5, 9, 7)), '2026-01-05 09:07')
  assert.equal(formatStamp(new Date(2026, 11, 31, 23, 59)), '2026-12-31 23:59')
})

test('scorePercent：0~1 → 0~100、边界夹紧、坏输入不炸', () => {
  assert.equal(scorePercent(0.876), 88)
  assert.equal(scorePercent(0), 0)
  assert.equal(scorePercent(1), 100)
  assert.equal(scorePercent(-0.5), 0) // 余弦可以为负 → 夹到 0
  assert.equal(scorePercent(1.7), 100) // 别让进度条冲出 100
  assert.equal(scorePercent(null), 0)
  assert.equal(scorePercent('abc'), 0) // NaN → 0，不炸模板
})

test('sourceScoreText：文档级命中（score=null）显示「整篇文档」，不是「相似度 0%」', () => {
  // 🔴 这是 Agent 文档任务（总结/对比）的来源形状：取的是整篇文档，没有相似度。
  // 直接走 scorePercent 会得到 0% —— 看起来像"完全不相关"，是误导。
  assert.equal(sourceScoreText(null), '整篇文档')
  assert.equal(sourceScoreText(undefined), '整篇文档')
  assert.equal(sourceScoreText(0.876), '相似度 88%')
  assert.equal(sourceScoreText(0), '相似度 0%') // 真的有 0 分与"没有分数"是两回事
})

test('文档任务来源在导出件里写「整篇文档」', () => {
  const md = build([
    {
      id: 2,
      role: 'assistant',
      data: {
        answer: '对比结果',
        sources: [
          { index: 1, document_name: '员工考勤与休假管理制度.md', page_number: null, score: null, snippet: '全文' },
        ],
      },
    },
  ])
  assert.ok(md.includes('- [1] 《员工考勤与休假管理制度.md》无页码，整篇文档'))
  assert.ok(!md.includes('相似度 0%'), '不能把"没有分数"渲染成 0%')
})
