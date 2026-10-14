<script setup>
/**
 * 概览页 —— 登录后的第一屏。
 *
 * 三条口径（都由后端 `stats_service` 决定，前端只负责展示）：
 *  1. 数字按**当前用户可见范围**算，不是全库 —— 员工看到的是他有权访问的那部分；
 *  2. 问答数只算**自己的**；
 *  3. 管理员多一块系统级指标（用户数 / 向量条数），非管理员拿到的 `admin` 是
 *     **null**，此时整块不渲染 —— 而不是显示"用户 0 人"这种假数字。
 *
 * 视觉上的三个决定：
 *  · **数字卡片改成"图标在左、数字在右"**：原来纯居中，一眼扫过去不构成阅读动线；
 *  · **文档状态用环形图而不是一排 tag**：tag 只能并排摆着，占比要心算；环形图
 *    一眼看出"失败占多少"。数据少时它退化成整圈一个色，也不难看（有 v-if 兜底）；
 *  · **提问趋势是手写 SVG 折线**（与环形图同一条零依赖路线）。它不是首屏必需，
 *    独立加载、失败静默 —— 概览主体不该被一张辅助图表拖垮。
 *    ⚠️ 空数据（7 天全 0）**不画线、换文案**：一条贴底的直线看起来像"使用量在
 *    衰减"，而事实是"还没积累出趋势"，两者对读者的含义相反（判定在 utils/trendChart.js）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Calendar,
  ChatDotRound,
  Collection,
  DataAnalysis,
  Document,
  User,
} from '@element-plus/icons-vue'

import { statsApi } from '../api'
import { useUserStore } from '../stores/user'
import { buildTrendChart, isTrendEmpty } from '../utils/trendChart'

const router = useRouter()
const user = useUserStore()

const loading = ref(false)
const data = ref(null)

// ---- 提问趋势（独立于 overview 加载）----
const trend = ref(null)
const trendBox = ref(null)   // 折线容器：实测宽度喂给 viewBox，保证 1:1 像素渲染
const chartW = ref(0)
let ro = null

const trendTotal = computed(() =>
  (trend.value?.question_trend || []).reduce((acc, p) => acc + (p.count || 0), 0)
)

// 数据已加载且非全零才算得出图；否则 null → 模板走空文案分支
const trendChart = computed(() => {
  const t = trend.value?.question_trend
  if (!t || isTrendEmpty(t) || !chartW.value) return null
  return buildTrendChart(t, { width: chartW.value })
})

// "真的没有数据"与"还没量到容器宽度"是两回事：前者出文案，后者只等一帧
// （混在一起会让图出现前先闪一下空文案）
const emptyTrend = computed(() => isTrendEmpty(trend.value?.question_trend))

// 状态 → 文案 + 颜色。**颜色只在这里定义一次**，环形图和图例共用（原来 tag 靠
// Element Plus 的 type 推断颜色，现在换成显式色值，两处才对得上）。
const DOC_STATUS = {
  completed: { text: '已完成', color: 'var(--el-color-success)' },
  processing: { text: '处理中', color: 'var(--el-color-warning)' },
  pending: { text: '待处理', color: 'var(--el-color-info)' },
  failed: { text: '失败', color: 'var(--el-color-danger)' },
}

// 文档按状态拆开显示，空状态不给（否则四个数字里三个是 0，反而看不出重点）
const docStats = computed(() => {
  const by = data.value?.documents?.by_status || {}
  return Object.keys(DOC_STATUS)
    .filter((k) => by[k])
    .map((k) => ({ key: k, ...DOC_STATUS[k], count: by[k] }))
})

/**
 * 环形图分段。SVG 圆周按 100 份算：`dasharray="占比 剩余"`，`dashoffset` 定起点，
 * 每段接上一段的末尾（25 是 100 份对应 25% 的那个偏移量）。
 *
 * ⚠️ total 为 0 时返回空数组 —— 模板 `v-if` 兜住，不画一个空圈。
 */
const donutSegments = computed(() => {
  const total = data.value?.documents?.total || 0
  if (!total) return []
  let acc = 0
  return docStats.value.map((s) => {
    const pct = (s.count / total) * 100
    const seg = { key: s.key, color: s.color, pct, offset: acc }
    acc += pct
    return seg
  })
})

async function load() {
  loading.value = true
  try {
    const body = await statsApi.overview()
    data.value = body.data
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

/**
 * 趋势单独加载：接口挂掉只丢这一块，概览主体照常 —— 与"首屏不该为一张
 * 辅助图表付代价"是同一个决定。失败**静默**（刷新重试即可），不弹提示。
 */
async function loadTrends() {
  try {
    const body = await statsApi.trends(7)
    trend.value = body.data
    // 数据回来元素才存在 —— 下一帧量宽、并挂上观察器（只挂一次）
    await nextTick()
    measure()
    if (!ro && trendBox.value && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(measure)
      ro.observe(trendBox.value)
    }
  } catch {
    /* 辅助图表，失败静默 */
  }
}

/** 用容器实测宽度喂 viewBox：1:1 像素渲染，文字和线宽都不缩放。 */
function measure() {
  const el = trendBox.value
  if (el) chartW.value = Math.max(240, Math.floor(el.clientWidth))
}

function reload() {
  load()
  loadTrends()
}

onMounted(reload)
onBeforeUnmount(() => ro?.disconnect())
</script>

<template>
  <div v-loading="loading" class="dash">
    <el-card>
      <template #header>
        <div class="card-head">
          <span>{{ user.displayName }}，欢迎回来</span>
          <el-button size="small" @click="reload">刷新</el-button>
        </div>
      </template>

      <template v-if="data">
        <div class="metrics">
          <div class="metric" @click="router.push('/knowledge')">
            <div class="metric-icon"><el-icon><Collection /></el-icon></div>
            <div class="metric-body">
              <div class="num">{{ data.scope.kb_count }}</div>
              <div class="label">
                知识库
                <span v-if="data.scope.unlimited" class="sub">（不受限）</span>
              </div>
            </div>
          </div>
          <div class="metric" @click="router.push('/documents')">
            <div class="metric-icon"><el-icon><Document /></el-icon></div>
            <div class="metric-body">
              <div class="num">{{ data.documents.total }}</div>
              <div class="label">文档</div>
            </div>
          </div>
          <div class="metric">
            <div class="metric-icon"><el-icon><ChatDotRound /></el-icon></div>
            <div class="metric-body">
              <div class="num">{{ data.questions.total }}</div>
              <div class="label">我的提问（累计）</div>
            </div>
          </div>
          <div class="metric">
            <div class="metric-icon"><el-icon><Calendar /></el-icon></div>
            <div class="metric-body">
              <div class="num">{{ data.questions.today }}</div>
              <div class="label">我的提问（今日）</div>
            </div>
          </div>
          <div v-if="data.admin" class="metric">
            <div class="metric-icon"><el-icon><User /></el-icon></div>
            <div class="metric-body">
              <div class="num">{{ data.admin.user_count }}</div>
              <div class="label">系统用户</div>
            </div>
          </div>
          <div v-if="data.admin" class="metric">
            <div class="metric-icon"><el-icon><DataAnalysis /></el-icon></div>
            <div class="metric-body">
              <div class="num">{{ data.admin.vector_count ?? '—' }}</div>
              <div class="label">
                向量条数
                <span v-if="data.admin.vector_count === null" class="sub">（Milvus 不可用）</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 文档状态分布：环形图 + 图例。一条文档都没有时整块不出现 -->
        <div v-if="donutSegments.length" class="chart-row">
          <div class="donut">
            <svg viewBox="0 0 42 42" class="donut-svg" aria-hidden="true">
              <!-- 底环：没有数据的那部分也是"圈"的一部分，颜色要退到几乎看不见 -->
              <circle
                cx="21" cy="21" r="15.9154943" fill="transparent"
                stroke="var(--el-border-color-lighter)" stroke-width="4.5"
              />
              <circle
                v-for="seg in donutSegments"
                :key="seg.key"
                cx="21" cy="21" r="15.9154943" fill="transparent"
                :stroke="seg.color" stroke-width="4.5"
                :stroke-dasharray="`${seg.pct} ${100 - seg.pct}`"
                :stroke-dashoffset="25 - seg.offset"
              />
            </svg>
            <div class="donut-center">
              <div class="donut-num">{{ data.documents.total }}</div>
              <div class="donut-label">文档</div>
            </div>
          </div>
          <div class="legend">
            <div v-for="s in docStats" :key="s.key" class="legend-item">
              <span class="dot" :style="{ background: s.color }" />
              <span class="legend-text">{{ s.text }}</span>
              <span class="legend-num">{{ s.count }}</span>
            </div>
          </div>
        </div>

        <!-- 提问趋势（近 7 天折线）。🔴 空数据不画线、换文案 —— 见文件头说明。
             数据由 statsApi.trends 独立加载，失败则整块不出现（静默）。 -->
        <div v-if="trend" ref="trendBox" class="trend-row">
          <div class="trend-head">
            <span class="trend-title">提问趋势</span>
            <span class="trend-sub">近 {{ trend.days }} 天 · 共 {{ trendTotal }} 次</span>
          </div>
          <div v-if="emptyTrend" class="trend-empty">
            最近 {{ trend.days }} 天还没有提问 —— 数据还太少，暂时看不出趋势
          </div>
          <svg v-else-if="trendChart" :viewBox="`0 0 ${chartW} 132`" class="trend-svg" aria-hidden="true">
            <!-- 0 值基线：让"贴着底"这件事本身可见 -->
            <line
              :x1="trendChart.left" :y1="trendChart.baseY"
              :x2="trendChart.right" :y2="trendChart.baseY"
              stroke="var(--el-border-color-lighter)" stroke-width="1"
            />
            <polyline
              :points="trendChart.points"
              fill="none"
              stroke="var(--el-color-primary)"
              stroke-width="2"
              stroke-linejoin="round"
              stroke-linecap="round"
            />
            <template v-for="d in trendChart.dots" :key="d.date">
              <circle :cx="d.x" :cy="d.y" r="3" fill="var(--el-color-primary)">
                <title>{{ d.date }}：{{ d.count }} 次</title>
              </circle>
              <text :x="d.x" :y="trendChart.labelY" text-anchor="middle" class="trend-label">{{ d.label }}</text>
            </template>
          </svg>
        </div>
      </template>
    </el-card>

    <div class="cols">
      <el-card>
        <template #header>
          <div class="card-head">
            <span>最近文档</span>
            <el-button size="small" text type="primary" @click="router.push('/documents')">全部 →</el-button>
          </div>
        </template>
        <el-empty v-if="!data?.recent_documents?.length" description="还没有文档" :image-size="60" />
        <el-table v-else :data="data.recent_documents" size="small">
          <el-table-column prop="filename" label="文件名" show-overflow-tooltip />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <span class="status-cell">
                <span
                  class="dot"
                  :style="{ background: (DOC_STATUS[row.status] || {}).color || 'var(--el-color-info)' }"
                />
                {{ (DOC_STATUS[row.status] || {}).text || row.status }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="块" width="60" />
        </el-table>
      </el-card>

      <el-card>
        <template #header>
          <div class="card-head">
            <span>最近提问</span>
            <el-button size="small" text type="primary" @click="router.push('/chat')">去问答 →</el-button>
          </div>
        </template>
        <el-empty v-if="!data?.recent_questions?.length" description="还没有提问" :image-size="60" />
        <div v-else class="q-list">
          <div
            v-for="(q, i) in data.recent_questions"
            :key="i"
            class="q-item"
            @click="router.push('/chat')"
          >
            <div class="q-text">{{ q.query }}</div>
            <div class="q-meta">来自「{{ q.conversation_title }}」</div>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}
/* 图标在左、数字在右 —— 居中排布扫过去不构成阅读动线 */
.metric {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  text-align: left;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}
.metric:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: var(--el-box-shadow-lighter);
}
.metric-icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex: none;
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 22px;
}
.num {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
}
.label {
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
.sub {
  font-size: 11px;
}
/* ---- 文档状态环形图 ---- */
.chart-row {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}
.donut {
  position: relative;
  width: 120px;
  height: 120px;
  flex: none;
}
/* 旋转 -90deg：SVG 圆默认从 3 点钟方向起笔，转一下才从 12 点开始 */
.donut-svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}
.donut-center {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  align-content: center;
}
.donut-num {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
}
.donut-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.legend {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 120px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
}
.legend-text {
  color: var(--el-text-color-regular);
}
.legend-num {
  margin-left: auto;
  font-weight: 600;
}
/* ---- 提问趋势（手写 SVG 折线） ---- */
.trend-row {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.trend-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 4px;
}
.trend-title {
  font-size: 13px;
  font-weight: 600;
}
.trend-sub {
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}
.trend-empty {
  padding: 20px 0 10px;
  text-align: center;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}
/* viewBox 用容器实测宽度，所以这里是 1:1 像素渲染 —— 文字不缩放 */
.trend-svg {
  display: block;
  width: 100%;
  height: 132px;
}
.trend-label {
  font-size: 10px;
  fill: var(--el-text-color-secondary);
}
/* 最近文档的状态列：色点 + 文字，比 tag 轻 */
.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--el-font-size-extra-small);
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
@media (max-width: 1000px) {
  .cols { grid-template-columns: 1fr; }
}
.q-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
}
.q-item:last-child {
  border-bottom: none;
}
.q-text {
  font-size: 13px;
  line-height: 1.5;
}
.q-meta {
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
</style>
