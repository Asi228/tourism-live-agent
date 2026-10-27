<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Calendar,
  ChatDotRound,
  CircleCheck,
  Collection,
  Refresh,
  Tickets,
  Warning,
} from '@element-plus/icons-vue'

import { commerceApi, rpaApi, statsApi } from '../api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const user = useUserStore()
const loading = ref(false)
const sessions = ref([])
const overview = ref(null)
const monitoring = ref(null)
const alerts = computed(() => monitoring.value?.alerts || [])

function fmtDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function sessionState(session) {
  const states = {
    draft: { text: '草稿', type: 'info' },
    checking: { text: '核验中', type: 'warning' },
    needs_confirmation: { text: '存在阻断', type: 'danger' },
    ready: { text: '可开播', type: 'success' },
    published: { text: '直播中', type: 'success' },
    finished: { text: '已结束', type: 'info' },
  }
  return states[session.status] || { text: session.status, type: 'info' }
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  return `${(seconds / 60).toFixed(1)} 分钟`
}

function alertTarget(alert) {
  if (alert.task_id) return '/automation'
  if (alert.approval_id) return '/approvals'
  return '/commerce'
}

async function load() {
  loading.value = true
  try {
    const [sessionBody, overviewBody, metricsBody] = await Promise.all([
      commerceApi.sessions(),
      statsApi.overview(),
      rpaApi.metrics(30),
    ])
    sessions.value = sessionBody.data || []
    overview.value = overviewBody.data || null
    monitoring.value = metricsBody.data || null
  } catch {
    // 统一错误提示由请求拦截器负责。
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="dashboard">
    <section class="hero">
      <div>
        <div class="eyebrow">今日运营总览</div>
        <h1>{{ user.displayName }}，欢迎进入运营工作台</h1>
        <p>集中查看直播准备、商品风险、待审批事项和自动处理结果，优先完成今天的重要工作。</p>
      </div>
      <div class="hero-actions">
        <el-button :icon="Refresh" @click="load">刷新数据</el-button>
        <el-button type="primary" @click="router.push('/commerce')">管理直播场次</el-button>
      </div>
    </section>

    <section class="metrics">
      <button class="metric" type="button" @click="router.push('/commerce')">
        <el-icon><Calendar /></el-icon>
        <div><strong>{{ monitoring?.sessions.ready || 0 }} / {{ monitoring?.sessions.total || 0 }}</strong><span>可开播 / 场次</span></div>
      </button>
      <button class="metric danger" type="button" @click="router.push('/commerce')">
        <el-icon><Warning /></el-icon>
        <div><strong>{{ monitoring?.sessions.blocked || 0 }}</strong><span>阻断场次</span></div>
      </button>
      <button class="metric" type="button" @click="router.push('/automation')">
        <el-icon><CircleCheck /></el-icon>
        <div><strong>{{ monitoring?.tasks.success_rate ?? '—' }}<small v-if="monitoring?.tasks.success_rate !== null">%</small></strong><span>自动处理成功率（30 天）</span></div>
      </button>
      <button class="metric danger" type="button" @click="router.push('/automation')">
        <el-icon><Warning /></el-icon>
        <div><strong>{{ monitoring?.tasks.failed || 0 }}</strong><span>处理失败（30 天）</span></div>
      </button>
      <button class="metric" type="button" @click="router.push('/automation')">
        <el-icon><Tickets /></el-icon>
        <div><strong class="duration">{{ durationText(monitoring?.tasks.avg_duration_seconds) }}</strong><span>平均处理时长</span></div>
      </button>
      <button class="metric attention" type="button" @click="router.push('/approvals')">
        <el-icon><Collection /></el-icon>
        <div><strong>{{ monitoring?.approvals.pending || 0 }}</strong><span>待审批申请</span></div>
      </button>
    </section>

    <section class="content-grid">
      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span>近期直播场次</span>
            <el-button link type="primary" @click="router.push('/commerce')">查看全部</el-button>
          </div>
        </template>
        <el-empty v-if="!sessions.length" description="还没有直播任务" :image-size="64">
          <el-button type="primary" @click="router.push('/commerce')">创建直播任务</el-button>
        </el-empty>
        <el-table v-else :data="sessions.slice(0, 6)" size="small">
          <el-table-column prop="title" label="任务" min-width="180" show-overflow-tooltip />
          <el-table-column label="计划开播" min-width="155">
            <template #default="{ row }">{{ fmtDate(row.starts_at) }}</template>
          </el-table-column>
          <el-table-column label="选品" width="70">
            <template #default="{ row }">{{ row.items?.length || 0 }}</template>
          </el-table-column>
          <el-table-column label="准备状态" width="130">
            <template #default="{ row }">
              <el-tag :type="sessionState(row).type" effect="plain">{{ sessionState(row).text }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <div class="card-head"><span>运营异常</span><span class="card-note">最新巡检、审批与任务</span></div>
        </template>
        <el-empty v-if="!alerts.length" description="当前没有待处理异常" :image-size="64" />
        <div v-else class="alerts">
          <div
            v-for="(item, index) in alerts.slice(0, 8)" :key="`${item.alert_type}-${index}`"
            class="alert-item" :class="item.severity" @click="router.push(alertTarget(item))"
          >
            <el-icon><Warning /></el-icon>
            <div><strong>{{ item.title }}</strong><span>{{ item.detail }} · {{ fmtDate(item.occurred_at) }}</span></div>
          </div>
        </div>
      </el-card>
    </section>

    <section class="ability-grid">
      <div class="ability ready" @click="router.push('/commerce')">
        <el-icon><CircleCheck /></el-icon>
        <div><strong>开播前检查</strong><span>检查库存、价格、活动、商品状态和旅游产品使用规则</span></div>
      </div>
      <div class="ability ready" @click="router.push('/automation')">
        <el-icon><Collection /></el-icon>
        <div><strong>自动化处理</strong><span>近 30 天共 {{ monitoring?.tasks.total || 0 }} 个任务，支持采集、改价、下架和结果核验</span></div>
      </div>
      <div class="ability ready" @click="router.push('/approvals')">
        <el-icon><Tickets /></el-icon>
        <div><strong>待办审批</strong><span>改价、下架等高风险操作经过人工复核并保留操作记录</span></div>
      </div>
      <div class="ability ready" @click="router.push('/chat')">
        <el-icon><ChatDotRound /></el-icon>
        <div><strong>业务知识助手</strong><span>从 {{ overview?.documents?.total || 0 }} 份商品、景区、酒店和售后资料中查找可靠答案</span></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: 16px; }
.hero {
  display: flex; align-items: center; justify-content: space-between; gap: 20px;
  padding: 24px 28px; border-radius: 12px; color: white;
  background: linear-gradient(120deg, #172554 0%, #1d4ed8 58%, #0891b2 100%);
}
.eyebrow { font-size: 11px; letter-spacing: .14em; opacity: .76; }
.hero h1 { margin: 7px 0 8px; font-size: 25px; }
.hero p { margin: 0; max-width: 720px; line-height: 1.65; opacity: .86; }
.hero-actions { display: flex; flex: none; gap: 10px; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.metric {
  display: flex; align-items: center; gap: 14px; padding: 18px; text-align: left;
  border: 1px solid var(--el-border-color-light); border-radius: 10px;
  background: var(--el-bg-color); color: var(--el-text-color-primary); cursor: pointer;
}
.metric:hover { border-color: var(--el-color-primary-light-5); box-shadow: var(--el-box-shadow-lighter); }
.metric .el-icon { font-size: 25px; color: var(--el-color-primary); }
.metric.attention .el-icon { color: var(--el-color-warning); }
.metric.danger .el-icon { color: var(--el-color-danger); }
.metric strong, .metric span { display: block; }
.metric strong { font-size: 24px; line-height: 1.2; }
.metric strong.duration { font-size: 18px; }
.metric small { margin-left: 2px; font-size: 13px; }
.metric span { margin-top: 4px; font-size: 12px; color: var(--el-text-color-secondary); }
.content-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, .65fr); gap: 16px; }
.card-head { display: flex; align-items: center; justify-content: space-between; font-weight: 600; }
.card-note { font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); }
.alerts { display: flex; flex-direction: column; gap: 10px; }
.alert-item { display: flex; gap: 10px; padding: 10px; border-radius: 8px; background: var(--el-color-warning-light-9); cursor: pointer; }
.alert-item.blocker { background: var(--el-color-danger-light-9); }
.alert-item.blocker .el-icon { color: var(--el-color-danger); }
.alert-item .el-icon { margin-top: 2px; color: var(--el-color-warning); }
.alert-item strong, .alert-item span { display: block; }
.alert-item strong { font-size: 13px; }
.alert-item span { margin-top: 4px; font-size: 12px; color: var(--el-text-color-secondary); }
.ability-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
.ability { display: flex; gap: 12px; padding: 16px; border: 1px solid var(--el-border-color-light); border-radius: 10px; background: var(--el-bg-color); cursor: pointer; }
.ability .el-icon { flex: none; margin-top: 2px; font-size: 20px; color: var(--el-color-primary); }
.ability strong, .ability span { display: block; }
.ability span { margin-top: 6px; font-size: 12px; line-height: 1.55; color: var(--el-text-color-secondary); }
.ability.ready { border-left: 3px solid var(--el-color-success); }
.ability.planned { cursor: default; border-style: dashed; }
.ability.planned .el-icon { color: var(--el-color-info); }
@media (max-width: 1100px) {
  .content-grid { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .hero { align-items: flex-start; flex-direction: column; }
  .metrics, .ability-grid { grid-template-columns: 1fr; }
}
</style>
