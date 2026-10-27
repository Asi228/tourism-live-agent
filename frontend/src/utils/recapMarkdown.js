const TASK_STATUS = {
  queued: '待执行', running: '执行中', succeeded: '成功', failed: '失败', queue_failed: '入队失败',
}
const TASK_TYPE = {
  collect_platform_data: '平台数据采集', price_fix: '价格修复', delist: '商品下架',
}
const APPROVAL_STATUS = {
  pending: '待审批', approved: '已通过待执行', rejected: '已驳回', executed: '已执行', failed: '执行失败',
}

function cell(value) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value).replaceAll('|', '\\|').replaceAll('\n', ' ')
}

function money(value) {
  return value === null || value === undefined ? '—' : `¥${Number(value).toFixed(2)}`
}

function seconds(value) {
  return value === null || value === undefined ? '—' : `${Number(value).toFixed(2)} 秒`
}

/** 将后端已聚合的单场复盘转换为可归档 Markdown。 */
export function buildSessionRecapMarkdown(report, stamp) {
  const inspection = report.inspection || {}
  const automation = report.automation || {}
  const approvals = report.approvals || {}
  const latest = inspection.latest
  const lines = [
    `# ${report.title}｜直播复盘`,
    '',
    `> 生成于 ${stamp} ｜ 场次 #${report.session_id} ｜ 计划开播 ${cell(report.starts_at)}`,
    '',
    `## 复盘结论`,
    '',
    report.headline,
    '',
    '## 业务概览',
    '',
    '| 指标 | 数值 |',
    '| --- | ---: |',
    `| 选品 SKU | ${report.sku_count} |`,
    `| 计划销量 | ${report.planned_sales} |`,
    `| 计划 GMV | ${money(report.planned_gmv)} |`,
    `| 巡检次数 | ${inspection.total_runs || 0} |`,
    `| 最新准备分 | ${latest ? latest.score : '—'} |`,
    `| RPA 成功率 | ${automation.success_rate == null ? '—' : `${automation.success_rate}%`} |`,
    `| RPA 平均耗时 | ${seconds(automation.avg_duration_seconds)} |`,
    `| 待审批 | ${approvals.pending || 0} |`,
    '',
    '## 本场选品',
    '',
  ]
  if (report.items?.length) {
    lines.push(
      '| SKU | 商品 | 规格 | 计划销量 | 计划单价 | 计划 GMV |',
      '| --- | --- | --- | ---: | ---: | ---: |',
    )
    for (const item of report.items) {
      lines.push(`| ${cell(item.sku_code)} | ${cell(item.product_name)} | ${cell(item.specification)} | ${item.planned_sales} | ${money(item.planned_unit_price)} | ${money(item.planned_gmv)} |`)
    }
  } else {
    lines.push('尚未添加选品。')
  }

  lines.push('', '## 最新巡检风险', '')
  if (inspection.unresolved_risks?.length) {
    lines.push('| 等级 | 检查项 | SKU | 问题 | 建议 |', '| --- | --- | --- | --- | --- |')
    for (const risk of inspection.unresolved_risks) {
      lines.push(`| ${risk.severity === 'blocker' ? '阻断' : '警告'} | ${cell(risk.rule_name)} | ${cell(risk.sku?.sku_code)} | ${cell(risk.message)} | ${cell(risk.suggestion)} |`)
    }
  } else {
    lines.push(latest ? '最新巡检没有未通过风险。' : '尚未执行巡检。')
  }

  lines.push(
    '', '## RPA 执行记录', '',
    `共 ${automation.total || 0} 个任务：成功 ${automation.succeeded || 0}，失败 ${automation.failed || 0}，重试任务 ${automation.retry_task_count || 0}。`,
    '',
  )
  if (automation.tasks?.length) {
    lines.push('| 任务 | 类型 | 状态 | 尝试 | 耗时 | 错误 |', '| ---: | --- | --- | ---: | ---: | --- |')
    for (const task of automation.tasks) {
      lines.push(`| #${task.id} | ${TASK_TYPE[task.task_type] || cell(task.task_type)} | ${TASK_STATUS[task.status] || cell(task.status)} | ${task.attempt_count} | ${seconds(task.duration_seconds)} | ${cell(task.error_message)} |`)
    }
  } else {
    lines.push('本场没有 RPA 执行记录。')
  }

  lines.push('', '## 审批闭环', '')
  if (approvals.approvals?.length) {
    lines.push('| 审批 | 操作 | SKU | 状态 | 决策耗时 | 关联任务 |', '| ---: | --- | --- | --- | ---: | ---: |')
    for (const row of approvals.approvals) {
      lines.push(`| #${row.id} | ${TASK_TYPE[row.task_type] || cell(row.task_type)} | ${cell(row.sku_code)} | ${APPROVAL_STATUS[row.status] || cell(row.status)} | ${seconds(row.decision_seconds)} | ${row.task_id ? `#${row.task_id}` : '—'} |`)
    }
  } else {
    lines.push('本场没有审批记录。')
  }
  lines.push('', '> 本报告按生成时的数据库记录汇总；当前项目尚未接入真实订单与退款数据，因此不展示成交额、退款率等结果指标。', '')
  return lines.join('\n')
}
