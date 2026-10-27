<template>
  <div class="automation">
    <el-card>
      <template #header>
        <div class="head">
          <div>
            <span>定时监控计划</span>
            <span class="hint">按固定间隔自动创建只读采集任务，采集完成后自动巡检</span>
          </div>
          <el-button type="primary" plain @click="openPlan()">配置监控</el-button>
        </div>
      </template>
      <el-table v-if="plans.length" :data="plans" size="small">
        <el-table-column label="直播场次" min-width="190">
          <template #default="{ row }">{{ sessionLabel(row.session_id) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? '运行中' : '已暂停' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="采集间隔" width="100">
          <template #default="{ row }">{{ intervalLabel(row.interval_minutes) }}</template>
        </el-table-column>
        <el-table-column label="下次执行" width="170">
          <template #default="{ row }">{{ row.enabled ? fmt(row.next_run_at) : '—' }}</template>
        </el-table-column>
        <el-table-column label="上次执行" width="170">
          <template #default="{ row }">{{ fmt(row.last_run_at) }}</template>
        </el-table-column>
        <el-table-column label="最近任务" width="90">
          <template #default="{ row }">
            <el-button v-if="row.last_task_id" link type="primary" @click="openTaskById(row.last_task_id)">
              #{{ row.last_task_id }}
            </el-button>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openPlan(row)">编辑</el-button>
            <el-button link :type="row.enabled ? 'warning' : 'success'" @click="setPlanEnabled(row, !row.enabled)">
              {{ row.enabled ? '暂停' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="还没有定时监控计划" :image-size="58" />
    </el-card>

    <el-card>
      <template #header>
        <div class="head">
          <div>
            <span>自动化任务</span>
            <span class="hint">自动采集平台商品、库存和价格，保存数据后执行开播检查</span>
          </div>
          <div>
            <el-button :loading="loading" @click="load">刷新</el-button>
            <el-button type="primary" @click="goCommerce">前往直播场次</el-button>
          </div>
        </div>
      </template>

      <el-table :data="tasks" v-loading="loading" stripe>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="场次" width="100">
          <template #default="{ row }">#{{ row.session_id }}</template>
        </el-table-column>
        <el-table-column label="类型" min-width="150">
          <template #default="{ row }">{{ typeLabel(row.task_type) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="attempt_count" label="尝试" width="70" />
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ fmt(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="完成时间" width="170">
          <template #default="{ row }">{{ fmt(row.completed_at) }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="180">
          <template #default="{ row }">
            <span v-if="row.error_message" class="err">{{ row.error_message }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button
              v-if="canRequeue(row)"
              link
              type="warning"
              @click="requeue(row)"
            >重新入队</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !tasks.length" description="还没有自动化任务，请先在直播场次中发起数据采集" />
    </el-card>

    <el-dialog v-model="planOpen" title="配置定时监控" width="480px">
      <el-form label-position="top">
        <el-form-item label="直播场次" required>
          <el-select v-model="planForm.session_id" style="width: 100%" :disabled="Boolean(editingPlan)">
            <el-option
              v-for="session in schedulableSessions" :key="session.id"
              :label="`#${session.id} ${session.title}`" :value="session.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="采集间隔" required>
          <el-select v-model="planForm.interval_minutes" style="width: 100%">
            <el-option v-for="item in intervals" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用计划">
          <el-switch v-model="planForm.enabled" active-text="启用" inactive-text="暂停" />
        </el-form-item>
        <el-alert
          title="调度器只创建平台数据采集任务；改价、下架仍必须经过人工审批。"
          type="info" :closable="false" show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="planOpen = false">取消</el-button>
        <el-button type="primary" :loading="savingPlan" @click="savePlan">保存计划</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" title="任务详情" size="640px">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="任务">#{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="场次">#{{ detail.session_id }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="statusTag(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="尝试次数">{{ detail.attempt_count }}</el-descriptions-item>
          <el-descriptions-item label="开始">{{ fmt(detail.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="完成">{{ fmt(detail.completed_at) }}</el-descriptions-item>
        </el-descriptions>

        <template v-if="detail.result && detail.result.before">
          <h4>执行与验证</h4>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="SKU">{{ detail.result.sku_code }}</el-descriptions-item>
            <el-descriptions-item label="执行前">
              {{ detail.result.before.live_price ?? detail.result.before.platform_status }}
            </el-descriptions-item>
            <el-descriptions-item label="预期值">
              {{ detail.result.expected?.live_price ?? detail.result.expected?.platform_status }}
            </el-descriptions-item>
            <el-descriptions-item label="平台实际">
              {{ detail.result.actual?.live_price ?? detail.result.actual?.platform_status }}
            </el-descriptions-item>
            <el-descriptions-item label="验证结果">
              <el-tag size="small" :type="detail.result.verified ? 'success' : 'danger'">
                {{ detail.result.verified ? '通过' : '未通过' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="审批单">#{{ detail.result.approval_id }}</el-descriptions-item>
          </el-descriptions>
        </template>

        <template v-else-if="detail.result">
          <h4>采集结果</h4>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="采集 SKU">{{ detail.result.collected?.length ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="写入快照">{{ detail.result.snapshots?.length ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="平台缺失">{{ detail.result.missing_sku_codes?.length ?? 0 }}</el-descriptions-item>
          </el-descriptions>
          <el-alert
            v-if="detail.result.missing_sku_codes?.length"
            type="warning"
            :closable="false"
            show-icon
            style="margin-top: 8px"
            :title="`平台未找到：${detail.result.missing_sku_codes.join('、')}`"
          />
          <el-alert
            v-if="detail.result.inspection?.status === 'completed'"
            type="info"
            :closable="false"
            show-icon
            style="margin-top: 8px"
            :title="`已自动巡检：${detail.result.inspection.conclusion}，准备分 ${detail.result.inspection.score}（阻断 ${detail.result.inspection.blocker_count} / 警告 ${detail.result.inspection.warning_count}）`"
          />
          <el-alert
            v-else-if="detail.result.inspection?.status === 'failed'"
            type="error"
            :closable="false"
            show-icon
            style="margin-top: 8px"
            :title="`巡检失败：${detail.result.inspection.error}`"
          />
        </template>

        <h4>执行步骤</h4>
        <el-table :data="detail.steps" size="small" border>
          <el-table-column label="步骤" min-width="180">
            <template #default="{ row }">
              <div>{{ row.step_name }}</div>
              <div class="muted">{{ row.step_code }}</div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'succeeded' ? 'success' : 'danger'">
                {{ row.status === 'succeeded' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="220">
            <template #default="{ row }">
              <span class="msg">{{ row.message || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="证据" width="80">
            <template #default="{ row }">
              <el-button
                v-if="row.screenshot_path"
                link
                type="primary"
                @click="openShot(row)"
              >截图</el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>

        <el-alert
          v-if="detail.error_message"
          type="error"
          :closable="false"
          show-icon
          style="margin-top: 12px"
          :title="detail.error_message"
        />
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { commerceApi, rpaApi } from '../api'

/**
 * 自动化任务页：RPA 任务的"观察窗"。
 *
 * 定位：这里**不发起任务** —— 采集的入口在直播任务详情（运营在业务上下文里点"采集"），
 * 本页负责看状态、看步骤证据、失败重排。把"触发"留在业务页，是为了让任务有依据
 * （必须选一场直播），而不是在空页面上随便跑一个采集。
 *
 * 有活跃任务时 10s 自动刷新一次；全部结束就停 —— 避免无意义的轮询。
 */

const router = useRouter()
const tasks = ref([])
const plans = ref([])
const sessions = ref([])
const loading = ref(false)
const planOpen = ref(false)
const savingPlan = ref(false)
const editingPlan = ref(null)
const planForm = reactive({ session_id: null, interval_minutes: 30, enabled: true })
const intervals = [
  { label: '每 5 分钟', value: 5 },
  { label: '每 15 分钟', value: 15 },
  { label: '每 30 分钟', value: 30 },
  { label: '每 1 小时', value: 60 },
  { label: '每 3 小时', value: 180 },
  { label: '每天', value: 1440 },
]
const schedulableSessions = computed(() =>
  sessions.value.filter((session) => session.items?.length && session.status !== 'finished'),
)
const detailOpen = ref(false)
const detail = ref(null)
let timer = null

const STATUS = {
  queued: { label: '待执行', tag: 'info' },
  running: { label: '执行中', tag: 'warning' },
  succeeded: { label: '成功', tag: 'success' },
  failed: { label: '失败', tag: 'danger' },
  queue_failed: { label: '入队失败', tag: 'danger' },
}
function statusLabel(s) {
  return STATUS[s]?.label || s
}
function statusTag(s) {
  return STATUS[s]?.tag || 'info'
}
function typeLabel(t) {
  return { collect_platform_data: '平台数据采集' }[t] || t
}
function fmt(v) {
  return v ? v.replace('T', ' ').slice(0, 19) : '—'
}
function canRequeue(row) {
  return ['failed', 'queue_failed'].includes(row.status)
}

function sessionLabel(sessionId) {
  const session = sessions.value.find((row) => row.id === sessionId)
  return session ? `#${session.id} ${session.title}` : `#${sessionId}`
}

function intervalLabel(minutes) {
  return intervals.find((item) => item.value === minutes)?.label || `${minutes} 分钟`
}

async function load() {
  loading.value = true
  try {
    const [taskBody, planBody, sessionBody] = await Promise.all([
      rpaApi.tasks(), rpaApi.monitoringPlans(), commerceApi.sessions(),
    ])
    tasks.value = taskBody.data || []
    plans.value = planBody.data || []
    sessions.value = sessionBody.data || []
    scheduleRefresh()
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function scheduleRefresh() {
  if (timer) clearTimeout(timer)
  const active = tasks.value.some((t) => ['queued', 'running'].includes(t.status))
  if (active) timer = setTimeout(load, 10000)
}

async function openDetail(row) {
  try {
    const body = await rpaApi.task(row.id)
    detail.value = body.data
    detailOpen.value = true
  } catch {
    /* 拦截器已提示 */
  }
}

async function openTaskById(taskId) {
  await openDetail({ id: taskId })
}

function openPlan(row = null) {
  editingPlan.value = row
  Object.assign(planForm, {
    session_id: row?.session_id || schedulableSessions.value[0]?.id || null,
    interval_minutes: row?.interval_minutes || 30,
    enabled: row?.enabled ?? true,
  })
  planOpen.value = true
}

async function savePlan() {
  if (!planForm.session_id) {
    ElMessage.warning('请选择已添加选品的直播场次')
    return
  }
  savingPlan.value = true
  try {
    await rpaApi.saveMonitoringPlan(planForm.session_id, {
      interval_minutes: planForm.interval_minutes,
      enabled: planForm.enabled,
    })
    ElMessage.success('定时监控计划已保存')
    planOpen.value = false
    await load()
  } finally {
    savingPlan.value = false
  }
}

async function setPlanEnabled(row, enabled) {
  try {
    await rpaApi.saveMonitoringPlan(row.session_id, {
      interval_minutes: row.interval_minutes,
      enabled,
    })
    ElMessage.success(enabled ? '监控计划已启用' : '监控计划已暂停')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

async function requeue(row) {
  try {
    await rpaApi.requeue(row.id)
    ElMessage.success('已重新入队，等待 worker 执行')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

function openShot(step) {
  window.open(rpaApi.screenshotUrl(detail.value.id, step.id), '_blank')
}

function goCommerce() {
  router.push('/commerce')
}

onMounted(load)
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.automation { display: flex; flex-direction: column; gap: 14px; }
.hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.err {
  color: var(--el-color-danger);
  font-size: 12px;
}
.msg {
  font-size: 12px;
  white-space: pre-wrap;
}
h4 {
  margin: 18px 0 10px;
}
</style>
