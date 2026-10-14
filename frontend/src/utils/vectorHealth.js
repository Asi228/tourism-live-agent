/**
 * 向量库自检的展示层纯逻辑。
 *
 * 为什么单独抽出来：`Knowledge.vue` 与 `System.vue` **两张卡片读同一个接口**，
 * 判断必须一致 —— 而"读不到"和"真的是 0"这两种状态在界面上长得一模一样时，
 * 会把人引去重灌本来没丢的数据（2026-10-14 就绕了这一圈）。
 * 所以这条规则只写一次，放在能被 `node --test` 直接跑的地方。
 */

/** 一个读数：`null` 表示**取失败**，不能显示成 0。 */
export function metricOrDash(value) {
  return value === null || value === undefined ? '—' : String(value)
}

const VISIBILITY = {
  ok: { type: 'success', label: '检索可读' },
  empty: { type: 'info', label: '尚无向量' },
  missing: { type: 'warning', label: '缺向量：去点批量向量化' },
  // 🔴 这一条是这次排查的关键：数据在库里，但检索读不到。
  // 处置方式和 missing **完全相反** —— 重灌向量只是临时治好，根治要在 Milvus 侧 load + flush。
  not_visible: { type: 'danger', label: '向量在但读不到（未加载/未落盘）' },
  partial: { type: 'warning', label: '部分向量不可见' },
  unknown: { type: 'info', label: '读数失败，结论不可信' },
}

/**
 * 把后端的 `visibility` 翻成 Element Plus 的 tag。
 * 认不出来的值一律降级成 info + 原值：自检卡片宁可说得保守，也不能报错把整块渲染打挂。
 */
export function visibilityView(visibility) {
  if (!visibility) return { type: 'info', label: '未自检' }
  const hit = VISIBILITY[visibility]
  return hit ? { ...hit } : { type: 'info', label: String(visibility) }
}

/** 不可用时（available=false）整张卡片的口径。 */
export function isVectorDown(health) {
  return Boolean(health) && health.available === false
}
