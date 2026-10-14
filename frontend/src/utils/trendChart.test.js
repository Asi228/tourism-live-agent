/**
 * 「提问趋势」折线图纯计算的守卫（`node --test`，零依赖）。
 *
 * 背景：`/stats/trends` 的后端接口与 `statsApi.trends` 早就存在，但**前端从未接入**
 * （死代码）。这次补上时把坐标计算抽成了纯函数 —— 它是最容易悄悄算错的地方：
 * y 轴方向写反（0 贴顶）、除零（单点 / 全零）、越界，任何一条都是"图看着怪但
 * 不报错"。这里把每个都钉住：
 *   1. 空态判定 —— 全零必须为 true（组件靠它换文案，不画贴底直线）；
 *   2. 首点贴左、末点贴右（x 均匀分布的边界）；
 *   3. **0 贴底、max 贴顶**（SVG 的 y 向下，翻转是经典错法）；
 *   4. 单点不除零、常量序列不越界。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildTrendChart, isTrendEmpty } from './trendChart.js'

const W = 600
const H = 132
// 与 trendChart.js 的默认值保持一致
const PAD_X = 12
const PAD_TOP = 12
const PAD_BOTTOM = 28
const PLOT_H = H - PAD_TOP - PAD_BOTTOM

/** 造一条趋势：counts 逐日对应，日期从 2026-10-08 起。 */
function makeTrend(counts) {
  return counts.map((count, i) => ({
    date: `2026-10-${String(8 + i).padStart(2, '0')}`,
    count,
  }))
}

// ---------- 空态 ----------

test('isTrendEmpty：全零 / 空 / null 都算空', () => {
  assert.equal(isTrendEmpty(makeTrend([0, 0, 0])), true)
  assert.equal(isTrendEmpty([]), true)
  assert.equal(isTrendEmpty(null), true)
  assert.equal(isTrendEmpty(undefined), true)
})

test('isTrendEmpty：只要有一天非零就不算空', () => {
  assert.equal(isTrendEmpty(makeTrend([0, 0, 1])), false)
  assert.equal(isTrendEmpty(makeTrend([5, 0, 0])), false)
})

// ---------- 基本几何 ----------

test('七天的 x 首点贴左、末点贴右、均匀分布', () => {
  const c = buildTrendChart(makeTrend([0, 1, 0, 3, 0, 2, 1]), { width: W, height: H })
  assert.equal(c.dots.length, 7)
  assert.equal(c.dots[0].x, PAD_X)
  assert.equal(c.dots[6].x, W - PAD_X)
  // 相邻等距（取一位小数后允许 0.1 误差）
  const step = (W - 2 * PAD_X) / 6
  for (let i = 1; i < 7; i++) {
    assert.ok(Math.abs((c.dots[i].x - c.dots[i - 1].x) - step) < 0.11, `第 ${i} 点间距漂了`)
  }
})

test('🔴 y 轴方向：0 贴底、最大值贴顶（翻转是最经典的错法）', () => {
  const c = buildTrendChart(makeTrend([0, 0, 3, 0]), { width: W, height: H })
  const zero = c.dots[0]
  const top = c.dots[2]
  assert.equal(zero.y, PAD_TOP + PLOT_H, '0 必须在底线')
  assert.equal(top.y, PAD_TOP, '最大值必须贴顶')
  assert.ok(zero.y > top.y, 'y 值：底 > 顶（SVG y 向下）')
  assert.equal(c.baseY, PAD_TOP + PLOT_H)
})

test('坐标不越界：全部落在绘图区内', () => {
  const c = buildTrendChart(makeTrend([0, 2, 1, 0, 5, 0, 3]), { width: W, height: H })
  for (const d of c.dots) {
    assert.ok(d.x >= PAD_X && d.x <= W - PAD_X, `x 越界：${d.x}`)
    assert.ok(d.y >= PAD_TOP && d.y <= PAD_TOP + PLOT_H, `y 越界：${d.y}`)
  }
})

// ---------- 边界 ----------

test('单点：不除零、水平居中', () => {
  const c = buildTrendChart(makeTrend([2]), { width: W, height: H })
  assert.equal(c.dots.length, 1)
  assert.equal(c.dots[0].x, W / 2)
  assert.equal(c.dots[0].y, PAD_TOP, '单点且是最大值 → 贴顶')
})

test('常量序列：全贴顶但仍有基线差（max 兜底 1 时全 0 不出现）', () => {
  const c = buildTrendChart(makeTrend([2, 2, 2]), { width: W, height: H })
  assert.ok(c.dots.every((d) => d.y === PAD_TOP), '全是最大值 → 全部贴顶')
  assert.equal(c.max, 2)
  assert.equal(c.total, 6)
})

test('total 与 max 统计正确（含补零日）', () => {
  const c = buildTrendChart(makeTrend([0, 1, 0, 3, 0, 2, 1]), { width: W, height: H })
  assert.equal(c.total, 7)
  assert.equal(c.max, 3)
})

// ---------- 输出形态 ----------

test('points 字符串与 dots 一一对应（模板画线靠它）', () => {
  const c = buildTrendChart(makeTrend([1, 0, 2]), { width: W, height: H })
  const coords = c.points.split(' ').map((s) => s.split(',').map(Number))
  assert.equal(coords.length, 3)
  c.dots.forEach((d, i) => {
    assert.equal(coords[i][0], d.x)
    assert.equal(coords[i][1], d.y)
  })
})

test('空趋势返回 null（组件据此渲染空文案而不是空图）', () => {
  assert.equal(buildTrendChart([], { width: W, height: H }), null)
  assert.equal(buildTrendChart(null, { width: W, height: H }), null)
})

test('label 取 MM-DD（去年份）', () => {
  const c = buildTrendChart(makeTrend([1, 1]), { width: W, height: H })
  assert.equal(c.dots[0].label, '10-08')
  assert.equal(c.dots[1].label, '10-09')
})

test('labelY 由函数给出（模板不硬编码 —— 改高度时标签不会错位）', () => {
  const c = buildTrendChart(makeTrend([1, 0, 2]), { width: W, height: H })
  assert.equal(c.labelY, H - 10)
  assert.ok(c.labelY > c.baseY, '标签在 0 值基线下方')
  assert.ok(c.labelY <= H, '标签不越出画布')
})
