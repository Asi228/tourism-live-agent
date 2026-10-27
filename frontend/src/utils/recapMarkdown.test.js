import test from 'node:test'
import assert from 'node:assert/strict'

import { buildSessionRecapMarkdown } from './recapMarkdown.js'

const report = {
  session_id: 8,
  title: '双十一洗护专场',
  starts_at: '2026-11-11T20:00:00',
  headline: '最新巡检仍有 1 个阻断项需要处理',
  sku_count: 1,
  planned_sales: 100,
  planned_gmv: '9900.00',
  items: [{
    sku_code: 'YL-001', product_name: '洗发水', specification: '450ml',
    planned_sales: 100, planned_unit_price: '99.00', planned_gmv: '9900.00',
  }],
  inspection: {
    total_runs: 2,
    latest: { score: 75 },
    unresolved_risks: [{
      severity: 'blocker', rule_name: '库存充足', sku: { sku_code: 'YL-001' },
      message: '库存不足|仅剩 20', suggestion: '补充库存',
    }],
  },
  automation: {
    total: 2, succeeded: 1, failed: 1, retry_task_count: 1,
    success_rate: 50, avg_duration_seconds: 12.5,
    tasks: [{
      id: 7, task_type: 'collect_platform_data', status: 'failed', attempt_count: 2,
      duration_seconds: 12.5, error_message: '登录失败',
    }],
  },
  approvals: {
    pending: 0,
    approvals: [{
      id: 3, task_type: 'price_fix', sku_code: 'YL-001', status: 'executed',
      decision_seconds: 30, task_id: 9,
    }],
  },
}

test('直播复盘导出包含业务结论、选品、巡检、RPA 和审批证据', () => {
  const md = buildSessionRecapMarkdown(report, '2026-10-23 12:30')
  assert.ok(md.includes('# 双十一洗护专场｜直播复盘'))
  assert.ok(md.includes('最新巡检仍有 1 个阻断项'))
  assert.ok(md.includes('| YL-001 | 洗发水 | 450ml | 100 | ¥99.00 | ¥9900.00 |'))
  assert.ok(md.includes('库存不足\\|仅剩 20'), '表格竖线必须转义')
  assert.ok(md.includes('| #7 | 平台数据采集 | 失败 | 2 | 12.50 秒 | 登录失败 |'))
  assert.ok(md.includes('| #3 | 价格修复 | YL-001 | 已执行 | 30.00 秒 | #9 |'))
  assert.ok(md.includes('尚未接入真实订单与退款数据'))
})

test('空数据使用明确文案，不输出 undefined 或虚假 0% 成功率', () => {
  const md = buildSessionRecapMarkdown({
    ...report,
    items: [],
    inspection: { total_runs: 0, latest: null, unresolved_risks: [] },
    automation: { total: 0, success_rate: null, avg_duration_seconds: null, tasks: [] },
    approvals: { pending: 0, approvals: [] },
  }, '2026-10-23 12:30')
  assert.ok(md.includes('尚未添加选品'))
  assert.ok(md.includes('尚未执行巡检'))
  assert.ok(md.includes('| RPA 成功率 | — |'))
  assert.ok(md.includes('本场没有 RPA 执行记录'))
  assert.ok(md.includes('本场没有审批记录'))
  assert.ok(!md.includes('undefined'))
})
