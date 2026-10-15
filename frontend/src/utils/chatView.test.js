/**
 * Chat.vue 抽出的三块纯逻辑的守卫（`node --test`，零依赖）。
 *
 * 背景：这三段原来长在 .vue 里，`npm test` 碰不到 ——
 * 而它们恰好是"载荷少传一个字段就静默出错"的高危区：
 *   · 没选库时传 kb_ids=[] → 后端按"范围为空"处理，静默搜不到东西；
 *   · 前端写死 top_k → .env 改了不生效；
 *   · meta 漏展开 → 答案性质标签不显示。
 * 搬进 utils 后这些分支才第一次有测试覆盖。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildChatPayload, conversationTitle, toChatMessage } from './chatView.js'

test('buildChatPayload：没选库时不传 kb_ids（空数组=范围为空，会静默搜不到）', () => {
  const p = buildChatPayload('年假几天', { selectedKbs: [], conversationId: null })
  assert.deepEqual(p, { query: '年假几天' })
  assert.ok(!('kb_ids' in p), '空数组绝不能传给后端')
})

test('buildChatPayload：不写死 top_k（配置项归后端）', () => {
  const p = buildChatPayload('年假几天', { selectedKbs: [1, 2], conversationId: 7 })
  assert.ok(!('top_k' in p), '前端写死 top_k 会把 RAG_TOP_K 配置架空')
  assert.deepEqual(p.kb_ids, [1, 2])
  assert.equal(p.conversation_id, 7, '有会话 id 才带得上文')
})

test('buildChatPayload：新对话不带 conversation_id（后端自动建）', () => {
  const p = buildChatPayload('你好', { selectedKbs: [1] })
  assert.ok(!('conversation_id' in p))
})

test('toChatMessage：assistant 消息按 meta.source 分模式', () => {
  const rag = toChatMessage({
    id: 2, role: 'assistant', content: '答案', sources: [], meta: { source: 'rag' },
  })
  assert.equal(rag.mode, 'rag')
  const agent = toChatMessage({
    id: 3, role: 'assistant', content: '答案', sources: [], meta: { source: 'agent' },
  })
  assert.equal(agent.mode, 'agent')
  // 没带 source 的老消息按 rag 渲染（Agent 面板自然不出现）
  assert.equal(toChatMessage({ id: 4, role: 'assistant', content: 'x', sources: [] }).mode, 'rag')
})

test('toChatMessage：meta 展开进 data（答案性质标签的唯一来源）', () => {
  const m = toChatMessage({
    id: 5, role: 'assistant', content: '满一年 5 天', sources: [],
    meta: { source: 'faq', answer_mode: 'faq', hit: true },
  })
  assert.equal(m.data.answer_mode, 'faq', 'meta 漏展开 = 标签永远不显示')
  assert.equal(m.data.hit, true)
  assert.deepEqual(m.data.sources, [])
})

test('toChatMessage：user 消息原样透传', () => {
  assert.deepEqual(toChatMessage({ id: 1, role: 'user', content: '年假几天' }), {
    id: 1, role: 'user', content: '年假几天',
  })
})

test('conversationTitle：没开始/找到/找不到三种情况', () => {
  assert.equal(conversationTitle(null, []), '新对话')
  assert.equal(conversationTitle(7, [{ id: 7, title: '年假相关' }]), '年假相关')
  // 刚建、列表还没刷：id 兜底，不能显示空白
  assert.equal(conversationTitle(9, [{ id: 7, title: '别的' }]), '对话 9')
})
