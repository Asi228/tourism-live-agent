/**
 * 「提问趋势」折线图的**纯计算** —— 不依赖 Vue / DOM，`node:test` 直接可跑。
 *
 * 拆出来的理由：坐标计算是这张图里唯一会算错的地方（除零、翻转、越界），
 * 而它们全是纯数学 —— 放进组件就只能靠肉眼盯图。
 */

/** 默认画布尺寸。宽度由调用方按容器实测宽度传入（1:1 像素，文字不缩放）。 */
export const DEFAULT_WIDTH = 600
export const DEFAULT_HEIGHT = 132

/**
 * 趋势是否"等于没有"——7 天一次提问都没有。
 *
 * 🔴 空的时候**必须换文案，不能画一条贴底的线**：一条压着 0 轴的直线
 * 看起来像"使用量在衰减"，而事实是"还没积累出可看的趋势"。
 * 这两件事对读者的含义完全相反。
 */
export function isTrendEmpty(trend) {
  return !trend?.length || trend.every((p) => !p.count)
}

/**
 * 把 `question_trend`（`[{date, count}, …]`，已补零、按日期升序）算成画线所需的坐标。
 *
 * 布局约定（与 Dashboard 的 `viewBox` 一致）：
 *   · x 均匀分布：首点贴 `padX`，末点贴 `width - padX`；
 *   · y 反向：**0 贴底线、max 贴顶**（SVG 的 y 轴向下，别用直觉写）；
 *   · 底部 `padBottom` 留给日期标签。
 *
 * @returns {null | {
 *   points: string,          // polyline 的 points 属性
 *   dots: Array<{x,y,date,count,label}>,  // 每个数据点（画圆点 + title）
 *   baseY: number,           // 0 值基线（画那条浅色底线）
 *   labelY: number,          // 日期标签的文字基线 y
 *   max: number, total: number,
 *   left: number, right: number,  // 基线的起止 x
 * }}
 */
export function buildTrendChart(trend, options = {}) {
  const {
    width = DEFAULT_WIDTH,
    height = DEFAULT_HEIGHT,
    padX = 12,
    padTop = 12,
    padBottom = 28,
  } = options

  if (!trend?.length) return null

  const n = trend.length
  // 防除零：全 0 时（虽然调用方会先判空，函数自身也不能炸）max 兜到 1
  const max = Math.max(1, ...trend.map((p) => p.count || 0))
  const total = trend.reduce((acc, p) => acc + (p.count || 0), 0)

  const plotH = height - padTop - padBottom
  const left = padX
  const right = width - padX
  // n=1 时 (n-1)=0 会除零 —— 单点场景直接居中
  const step = n > 1 ? (right - left) / (n - 1) : 0

  const dots = trend.map((p, i) => {
    const count = p.count || 0
    const x = n > 1 ? left + step * i : width / 2
    const y = padTop + (1 - count / max) * plotH
    // 标签只取 MM-DD（`2026-10-08` → `10-08`），全年份重复且占宽
    const label = String(p.date || '').slice(5) || '—'
    return {
      x: round1(x),
      y: round1(y),
      date: p.date,
      count,
      label,
    }
  })

  return {
    points: dots.map((d) => `${d.x},${d.y}`).join(' '),
    dots,
    baseY: round1(padTop + plotH),
    // 日期标签的文字基线（距画布底 10px）—— 由函数给出，模板不该硬编码，
    // 否则改高度/边距时标签会错位，而这类错位测试抓不到（模板不测）
    labelY: round1(height - 10),
    max,
    total,
    left,
    right,
  }
}

/** 保留一位小数：坐标不需要更高精度，短字符串也让产物小一点。 */
function round1(v) {
  return Math.round(v * 10) / 10
}
