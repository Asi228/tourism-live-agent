/**
 * `vectorHealth.js` 的纯逻辑测试（`node --test`，零依赖）。
 * 重点只有一件事：**"读不到"绝不能显示成 0**，以及
 * `not_visible`（数据在但检索读不到）必须和 `missing`（真的缺数据）区分开 ——
 * 这两件事的处置方式相反，混在一起就会让人去重灌没丢的数据。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { isVectorDown, metricOrDash, visibilityView } from './vectorHealth.js'

test('metricOrDash：null/undefined 是"读不到"，显示成 — 而不是 0', () => {
  assert.equal(metricOrDash(null), '—')
  assert.equal(metricOrDash(undefined), '—')
  assert.equal(metricOrDash(0), '0') // 真的有 0 条与"没有数字"是两回事
  assert.equal(metricOrDash(27), '27')
})

test('visibilityView：not_visible 与 missing 必须是不同颜色、不同说法', () => {
  const nv = visibilityView('not_visible')
  const miss = visibilityView('missing')
  assert.equal(nv.type, 'danger')
  assert.equal(miss.type, 'warning')
  assert.notEqual(nv.label, miss.label)
  assert.match(nv.label, /未加载|未落盘/) // 要说清楚是"读不到"，不是"没有"
})

test('visibilityView：六种结论都有映射，不会漏成空白标签', () => {
  for (const key of ['ok', 'empty', 'missing', 'not_visible', 'partial', 'unknown']) {
    const v = visibilityView(key)
    assert.ok(v.label, `${key} 没有文案`)
    assert.ok(['success', 'info', 'warning', 'danger'].includes(v.type), `${key} 颜色非法`)
  }
})

test('visibilityView：后端加了新枚举值时降级显示原值，而不是渲染崩掉', () => {
  assert.deepEqual(visibilityView('brand_new_state'), { type: 'info', label: 'brand_new_state' })
  assert.equal(visibilityView(null).label, '未自检')
})

test('isVectorDown：只有 available===false 才算不可用，未自检不算', () => {
  assert.equal(isVectorDown({ available: false }), true)
  assert.equal(isVectorDown({ available: true }), false)
  assert.equal(isVectorDown(null), false)
})
