/**
 * 「答案性质」展示映射的守卫（`node --test`，零依赖 —— 见 package.json 的 test 脚本）。
 *
 * 背景：这块在 Chat.vue 里漏接过一次 —— 流式的 meta/done 回调都没赋
 * answer_mode，导致正常回答永远看不到那几个标签。逻辑搬到 utils 后，
 * 测试第一次能碰到它。这里钉两件事：
 *   1. 后端已知取值都有文案（删掉任何一条映射 = 红）；
 *   2. 未知取值降级显示原始串（后端加了新取值时，界面显示英文串而不是空白/崩溃）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { ANSWER_MODE, answerModeTag } from './answerMode.js'

// 与后端 `app/schemas/chat.py` / `agent.py` 的 answer_mode 取值对齐。
// ⚠️ 后端加了新取值而前端没补映射 → **下面第一条不会红**（它只查"已列出的都在"）。
// 这是刻意的：那种漂移由"界面显不显示原始串"来暴露（见第三条用例），
// 强行让测试红会逼着每次后端加值都改前端测试，反而没人跑它。
// （但"后端确实加了值"这个事实，应该主动把它补进这份清单。）
const BACKEND_MODES = ['grounded', 'no_evidence', 'smalltalk', 'faq', 'catalog']

test('后端的 answer_mode 取值都有展示映射', () => {
  for (const mode of BACKEND_MODES) {
    assert.ok(ANSWER_MODE[mode], `缺少 ${mode} 的映射`)
    assert.ok(ANSWER_MODE[mode].text, `${mode} 的 text 为空`)
    assert.ok(ANSWER_MODE[mode].type, `${mode} 的 type 为空`)
  }
})

test('catalog（文档清单）与 faq 一样是独立口径，不复用其他文案', () => {
  const catalog = answerModeTag('catalog')
  assert.equal(catalog.text, '文档清单')
  assert.notEqual(catalog.text, ANSWER_MODE.grounded.text)
  assert.notEqual(catalog.text, ANSWER_MODE.no_evidence.text)
  assert.notEqual(catalog.text, ANSWER_MODE.faq.text)
})

test('faq 是独立口径，不是 grounded / no_evidence 的复用', () => {
  const faq = answerModeTag('faq')
  assert.equal(faq.text, '来自常见问题')
  // 三种"依据来源"的文案两两不能撞 —— 撞了说明有人图省事复用了同一句
  assert.notEqual(faq.text, ANSWER_MODE.grounded.text)
  assert.notEqual(faq.text, ANSWER_MODE.no_evidence.text)
})

test('未知取值降级显示原始串 + info 样式（不返回 undefined）', () => {
  assert.deepEqual(answerModeTag('brand_new_mode'), {
    text: 'brand_new_mode',
    type: 'info',
  })
})

test('原型键不误命中（Object.hasOwn 存在的意义）', () => {
  // `ANSWER_MODE['constructor']` 直接取会拿到一个**函数**（truthy，来自
  // Object.prototype）—— 那是原型链上的东西，不是我们的映射。
  // 用 hasOwn 才能保证这类输入走降级分支而不是渲染出一个函数。
  assert.deepEqual(answerModeTag('constructor'), {
    text: 'constructor',
    type: 'info',
  })
  assert.deepEqual(answerModeTag('toString'), {
    text: 'toString',
    type: 'info',
  })
})
