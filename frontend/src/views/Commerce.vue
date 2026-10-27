<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Box, Calendar, ChatDotRound, DocumentAdd, Download, Plus, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { commerceApi, rpaApi } from '../api'
import { useUserStore } from '../stores/user'
import { buildSessionRecapMarkdown } from '../utils/recapMarkdown'

const user = useUserStore()
const router = useRouter()
const activeTab = ref('sessions')
const loading = ref(false)
const products = ref([])
const sessions = ref([])
const inventory = ref([])
const promotions = ref([])
const inspectionRules = ref([])
const selectedSessionId = ref(null)
const inspections = ref([])
const inspectionLoading = ref(false)
const collectionLoading = ref(false)
const agentLoading = ref(false)
const agentQuestion = ref('为什么本场直播现在不能开播？请按优先级给出处理建议。')
const agentResult = ref(null)
const approvalDraftOpen = ref(false)
const approvalDraftLoading = ref(false)
const approvalDraft = ref(null)
const approvalDraftReason = ref('')
const recapOpen = ref(false)
const recapLoading = ref(false)
const recap = ref(null)

const sessionDialog = ref(false)
const productDialog = ref(false)
const skuDialog = ref(false)
const itemDialog = ref(false)
const inventoryDialog = ref(false)
const promotionDialog = ref(false)
const saving = ref(false)
const skuProduct = ref(null)
const itemSession = ref(null)

function localDateTime(offsetDays = 1) {
  const date = new Date(Date.now() + offsetDays * 86400000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

const sessionForm = reactive({ title: '', starts_at: localDateTime() })
const productForm = reactive({ name: '', brand: '', category: '' })
const skuForm = reactive({ sku_code: '', specification: '', list_price: null, currency: 'CNY' })
const itemForm = reactive({
  sku_id: null, planned_sales: 0, planned_unit_price: null, sort_order: 0,
})
const inventoryForm = reactive({
  sku_id: null, available_quantity: 0, reserved_quantity: 0, source: 'supplier-system',
})
const promotionForm = reactive({
  name: '', promotion_type: 'live_price', sku_ids: [], fixed_price: null,
  threshold_amount: 0, discount_amount: null, stackable: false,
  starts_at: localDateTime(0), ends_at: localDateTime(30),
})
const priceForm = reactive({ sku_id: null, quantity: 1 })
const priceResult = ref(null)

const sessionStatus = {
  draft: { text: '草稿', type: 'info' },
  checking: { text: '核验中', type: 'warning' },
  needs_confirmation: { text: '待确认', type: 'danger' },
  ready: { text: '已就绪', type: 'success' },
  published: { text: '已发布', type: 'success' },
  finished: { text: '已结束', type: 'info' },
}

const packageStatus = {
  draft: { text: '草稿', type: 'info' },
  stale: { text: '需重检', type: 'danger' },
  published: { text: '已发布', type: 'success' },
}

const promotionTypes = {
  live_price: '直播价',
  threshold_discount: '满减',
  coupon: '优惠券',
}

const selectedSession = computed(() =>
  sessions.value.find((row) => row.id === selectedSessionId.value) || null,
)
const latestInspection = computed(() => inspections.value[0] || null)
const failedInspectionResults = computed(() =>
  (latestInspection.value?.results || []).filter((row) => row.status === 'failed'),
)

const skuOptions = computed(() =>
  products.value.flatMap((product) =>
    (product.skus || [])
      .filter((sku) => sku.status === 'active' && product.status === 'active')
      .map((sku) => ({
        ...sku,
        label: `${product.name} · ${sku.specification}（${sku.sku_code}）`,
      })),
  ),
)

const totalSkuCount = computed(() =>
  products.value.reduce((sum, product) => sum + (product.skus?.length || 0), 0),
)

const draftPackageCount = computed(() =>
  sessions.value.reduce(
    (sum, session) => sum + (session.packages || []).filter((p) => p.status === 'draft').length,
    0,
  ),
)

function fmtDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function money(value) {
  return `¥${Number(value || 0).toFixed(2)}`
}

function metric(value, suffix = '') {
  return value === null || value === undefined ? '—' : `${value}${suffix}`
}

function taskTypeLabel(value) {
  return { collect_platform_data: '平台数据采集', price_fix: '价格修复', delist: '商品下架' }[value] || value
}

function taskStatusLabel(value) {
  return {
    queued: '待执行', running: '执行中', succeeded: '成功', failed: '失败', queue_failed: '入队失败',
  }[value] || value
}

function approvalStatusLabel(value) {
  return {
    pending: '待审批', approved: '已通过待执行', rejected: '已驳回', executed: '已执行', failed: '执行失败',
  }[value] || value
}

async function openRecap(session) {
  recapLoading.value = true
  recapOpen.value = true
  recap.value = null
  try {
    const body = await commerceApi.recap(session.id)
    recap.value = body.data
  } catch {
    recapOpen.value = false
  } finally {
    recapLoading.value = false
  }
}

function exportRecap() {
  if (!recap.value) return
  const stamp = new Date().toLocaleString('zh-CN', { hour12: false })
  const markdown = buildSessionRecapMarkdown(recap.value, stamp)
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${recap.value.title.replace(/[\\/:*?"<>|]/g, '_')}-直播复盘.md`
  anchor.click()
  URL.revokeObjectURL(url)
}

async function loadAll() {
  loading.value = true
  try {
    const [productResult, sessionResult, inventoryResult, promotionResult, ruleResult] = await Promise.allSettled([
      commerceApi.products(),
      commerceApi.sessions(),
      commerceApi.inventory(),
      commerceApi.promotions(),
      commerceApi.inspectionRules(),
    ])
    if (productResult.status === 'fulfilled') products.value = productResult.value.data || []
    if (sessionResult.status === 'fulfilled') sessions.value = sessionResult.value.data || []
    if (inventoryResult.status === 'fulfilled') inventory.value = inventoryResult.value.data || []
    if (promotionResult.status === 'fulfilled') promotions.value = promotionResult.value.data || []
    if (ruleResult.status === 'fulfilled') inspectionRules.value = ruleResult.value.data || []
    if (selectedSessionId.value && !selectedSession.value) selectedSessionId.value = null
  } finally {
    loading.value = false
  }
}

async function loadInspections(sessionId) {
  if (!sessionId) {
    inspections.value = []
    return
  }
  inspectionLoading.value = true
  try {
    const body = await commerceApi.inspections(sessionId)
    inspections.value = body.data || []
  } finally {
    inspectionLoading.value = false
  }
}

async function runInspection(session) {
  inspectionLoading.value = true
  try {
    const body = await commerceApi.runInspection(session.id)
    await loadAll()
    selectedSessionId.value = session.id
    await loadInspections(session.id)
    agentResult.value = null
    ElMessage.success(
      body.data.conclusion === 'ready' ? '巡检通过，可以进入开播准备' : '巡检完成，请处理阻断项',
    )
  } finally {
    inspectionLoading.value = false
  }
}

async function askSessionAgent(question = agentQuestion.value) {
  const text = String(question || '').trim()
  if (!text) {
    ElMessage.warning('请输入要分析的问题')
    return
  }
  agentQuestion.value = text
  agentLoading.value = true
  try {
    const body = await commerceApi.explainSession(selectedSession.value.id, { question: text })
    agentResult.value = body.data
  } catch {
    /* 拦截器已提示 */
  } finally {
    agentLoading.value = false
  }
}

async function openApprovalDraft(action) {
  approvalDraftLoading.value = true
  try {
    const body = await commerceApi.approvalDraft(selectedSession.value.id, action.risk_id)
    approvalDraft.value = body.data
    approvalDraftReason.value = body.data.reason || ''
    approvalDraftOpen.value = true
  } catch {
    /* 拦截器已提示 */
  } finally {
    approvalDraftLoading.value = false
  }
}

async function submitApprovalDraft() {
  if (!approvalDraft.value?.can_submit) return
  approvalDraftLoading.value = true
  try {
    const draft = approvalDraft.value
    const body = await rpaApi.createApproval({
      session_id: draft.session_id,
      task_type: draft.task_type,
      sku_id: draft.sku_id,
      expected_value: draft.expected_value,
      reason: approvalDraftReason.value.trim() || null,
      source_inspection_result_id: draft.source_inspection_result_id,
    })
    approvalDraftOpen.value = false
    ElMessage.success(`审批申请 #${body.data.id} 已提交，请到“风险与审批”处理`)
    await askSessionAgent(agentQuestion.value)
  } catch {
    /* 拦截器已提示 */
  } finally {
    approvalDraftLoading.value = false
  }
}

function goApprovals() {
  approvalDraftOpen.value = false
  router.push('/approvals')
}

async function collectPlatformData(session) {
  collectionLoading.value = true
  try {
    const body = await rpaApi.createCollect(session.id)
    ElMessage.success(`采集任务 #${body.data.id} 已入队，到「自动化任务」查看进度`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    collectionLoading.value = false
  }
}

function openInventory() {
  Object.assign(inventoryForm, {
    sku_id: null, available_quantity: 0, reserved_quantity: 0, source: 'supplier-system',
  })
  inventoryDialog.value = true
}

async function submitInventory() {
  if (!inventoryForm.sku_id) {
    ElMessage.warning('请选择 SKU')
    return
  }
  saving.value = true
  try {
    await commerceApi.createInventory({ ...inventoryForm })
    inventoryDialog.value = false
    await loadAll()
    ElMessage.success('库存快照已记录')
  } finally {
    saving.value = false
  }
}

function openPromotion() {
  Object.assign(promotionForm, {
    name: '', promotion_type: 'live_price', sku_ids: [], fixed_price: null,
    threshold_amount: 0, discount_amount: null, stackable: false,
    starts_at: localDateTime(0), ends_at: localDateTime(30),
  })
  promotionDialog.value = true
}

async function submitPromotion() {
  if (!promotionForm.name.trim() || !promotionForm.sku_ids.length) {
    ElMessage.warning('请填写活动名称并选择适用 SKU')
    return
  }
  if (promotionForm.promotion_type === 'live_price' && !promotionForm.fixed_price) {
    ElMessage.warning('请填写直播价')
    return
  }
  if (promotionForm.promotion_type !== 'live_price' && !promotionForm.discount_amount) {
    ElMessage.warning('请填写优惠金额')
    return
  }
  saving.value = true
  try {
    await commerceApi.createPromotion({
      ...promotionForm,
      name: promotionForm.name.trim(),
      fixed_price: promotionForm.promotion_type === 'live_price' ? promotionForm.fixed_price : null,
      threshold_amount: promotionForm.promotion_type === 'live_price' ? null : promotionForm.threshold_amount,
      discount_amount: promotionForm.promotion_type === 'live_price' ? null : promotionForm.discount_amount,
    })
    promotionDialog.value = false
    await loadAll()
    ElMessage.success('促销活动已创建')
  } finally {
    saving.value = false
  }
}

async function previewPrice() {
  if (!priceForm.sku_id) {
    ElMessage.warning('请选择要计算的 SKU')
    return
  }
  saving.value = true
  try {
    const body = await commerceApi.previewPrice({ ...priceForm })
    priceResult.value = body.data
  } finally {
    saving.value = false
  }
}

function promotionRule(row) {
  if (row.promotion_type === 'live_price') return `直播价 ${money(row.fixed_price)}`
  const threshold = Number(row.threshold_amount || 0)
  return `${threshold > 0 ? `满 ${money(threshold)} ` : ''}减 ${money(row.discount_amount)}`
}

const ruleSeverityText = { blocker: '阻断', warning: '警告', info: '提示' }
const ruleSeverityType = { blocker: 'danger', warning: 'warning', info: 'info' }

async function saveInspectionRule(row, patch) {
  try {
    const body = await commerceApi.updateInspectionRule(row.rule_code, patch)
    const index = inspectionRules.value.findIndex((item) => item.rule_code === row.rule_code)
    if (index >= 0) inspectionRules.value[index] = body.data
    ElMessage.success(`规则“${row.rule_name}”已更新`)
  } catch {
    const body = await commerceApi.inspectionRules().catch(() => null)
    if (body) inspectionRules.value = body.data || []
  }
}

function updateRuleEnabled(row, enabled) {
  saveInspectionRule(row, { enabled })
}

function updateRuleSeverity(row, severity) {
  saveInspectionRule(row, { severity })
}

function updateInventoryAge(row, value) {
  saveInspectionRule(row, { parameters: { ...row.parameters, max_age_hours: value } })
}

function openCreateSession() {
  Object.assign(sessionForm, { title: '', starts_at: localDateTime() })
  sessionDialog.value = true
}

async function submitSession() {
  if (!sessionForm.title.trim() || !sessionForm.starts_at) {
    ElMessage.warning('请填写场次名称和开播时间')
    return
  }
  saving.value = true
  try {
    const body = await commerceApi.createSession({
      title: sessionForm.title.trim(),
      starts_at: sessionForm.starts_at,
    })
    sessionDialog.value = false
    await loadAll()
    selectedSessionId.value = body.data.id
    ElMessage.success('直播场次已创建')
  } finally {
    saving.value = false
  }
}

function openCreateProduct() {
  Object.assign(productForm, { name: '', brand: '', category: '' })
  productDialog.value = true
}

async function submitProduct() {
  if (!productForm.name.trim()) {
    ElMessage.warning('请填写商品名称')
    return
  }
  saving.value = true
  try {
    await commerceApi.createProduct({
      name: productForm.name.trim(),
      brand: productForm.brand.trim() || null,
      category: productForm.category.trim() || null,
    })
    productDialog.value = false
    await loadAll()
    ElMessage.success('商品已创建')
  } finally {
    saving.value = false
  }
}

function openCreateSku(product) {
  skuProduct.value = product
  Object.assign(skuForm, {
    sku_code: '', specification: '', list_price: null, currency: 'CNY',
  })
  skuDialog.value = true
}

async function submitSku() {
  if (!skuForm.sku_code.trim() || !skuForm.specification.trim() || !skuForm.list_price) {
    ElMessage.warning('请完整填写 SKU 编码、规格和价格')
    return
  }
  saving.value = true
  try {
    await commerceApi.createSku(skuProduct.value.id, {
      sku_code: skuForm.sku_code.trim(),
      specification: skuForm.specification.trim(),
      list_price: skuForm.list_price,
      currency: skuForm.currency,
    })
    skuDialog.value = false
    await loadAll()
    ElMessage.success('SKU 已创建')
  } finally {
    saving.value = false
  }
}

function openAddItem(session) {
  itemSession.value = session
  Object.assign(itemForm, {
    sku_id: null, planned_sales: 0, planned_unit_price: null,
    sort_order: session.items?.length || 0,
  })
  itemDialog.value = true
}

watch(selectedSessionId, (sessionId) => {
  agentResult.value = null
  loadInspections(sessionId)
})

async function submitItem() {
  if (!itemForm.sku_id) {
    ElMessage.warning('请选择 SKU')
    return
  }
  saving.value = true
  try {
    await commerceApi.addSessionItem(itemSession.value.id, { ...itemForm })
    itemDialog.value = false
    await loadAll()
    selectedSessionId.value = itemSession.value.id
    ElMessage.success('选品已添加')
  } finally {
    saving.value = false
  }
}

async function createPackage(session) {
  saving.value = true
  try {
    await commerceApi.createPackage(session.id)
    await loadAll()
    selectedSessionId.value = session.id
    ElMessage.success('准备包草稿已创建')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    await user.ensureProfile()
  } catch {
    return
  }
  await loadAll()
})
</script>

<template>
  <div class="commerce" v-loading="loading">
    <section class="hero">
      <div>
        <div class="eyebrow">旅游直播运营</div>
        <h1>直播场次与商品管理</h1>
        <p>统一安排旅游直播场次、门票与酒店套餐，并在开播前检查价格、库存和使用规则。</p>
      </div>
      <div class="hero-actions">
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreateSession">创建场次</el-button>
      </div>
    </section>

    <div class="metrics">
      <div class="metric-card">
        <el-icon><Calendar /></el-icon>
        <div><strong>{{ sessions.length }}</strong><span>我的直播场次</span></div>
      </div>
      <div class="metric-card">
        <el-icon><Box /></el-icon>
        <div><strong>{{ products.length }} / {{ totalSkuCount }}</strong><span>旅游产品 / 售卖规格</span></div>
      </div>
      <div class="metric-card">
        <el-icon><DocumentAdd /></el-icon>
        <div><strong>{{ draftPackageCount }}</strong><span>准备包草稿</span></div>
      </div>
    </div>

    <el-card shadow="never" class="workspace">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="直播场次" name="sessions">
          <el-empty v-if="!sessions.length" description="还没有直播场次，先创建一场直播" />
          <el-table
            v-else
            :data="sessions"
            row-key="id"
            highlight-current-row
            @current-change="(row) => { if (row) selectedSessionId = row.id }"
          >
            <el-table-column prop="title" label="场次" min-width="200" />
            <el-table-column label="开播时间" min-width="170">
              <template #default="{ row }">{{ fmtDate(row.starts_at) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="sessionStatus[row.status]?.type || 'info'" effect="plain">
                  {{ sessionStatus[row.status]?.text || row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="选品" width="80">
              <template #default="{ row }">{{ row.items?.length || 0 }}</template>
            </el-table-column>
            <el-table-column label="准备包版本" width="110">
              <template #default="{ row }">
                {{ row.packages?.length ? `v${row.packages[row.packages.length - 1].version}` : '—' }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="320" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click.stop="selectedSessionId = row.id">查看</el-button>
                <el-button link type="primary" @click.stop="openAddItem(row)">添加选品</el-button>
                <el-button
                  link type="primary" :disabled="!row.items?.length" :loading="collectionLoading"
                  @click.stop="collectPlatformData(row)"
                >采集平台数据</el-button>
                <el-button
                  link type="primary" :disabled="!row.items?.length" :loading="inspectionLoading"
                  @click.stop="runInspection(row)"
                >运行巡检</el-button>
                <el-button
                  link type="primary" :disabled="!row.items?.length" :loading="saving"
                  @click.stop="createPackage(row)"
                >创建准备包</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="selectedSession" class="session-detail">
            <div class="detail-head">
              <div>
                <h3>{{ selectedSession.title }}</h3>
                <span>负责人 ID {{ selectedSession.owner_id }} · {{ fmtDate(selectedSession.starts_at) }}</span>
              </div>
              <div>
                <el-button :icon="DocumentAdd" @click="openRecap(selectedSession)">查看复盘</el-button>
                <el-button type="primary" plain :icon="Plus" @click="openAddItem(selectedSession)">
                  添加选品
                </el-button>
              </div>
            </div>

            <div class="detail-grid">
              <div class="detail-panel">
                <div class="panel-title">本场选品</div>
                <el-empty
                  v-if="!selectedSession.items?.length" description="尚未添加商品" :image-size="56"
                />
                <div v-else class="item-list">
                  <div v-for="item in selectedSession.items" :key="item.id" class="item-row">
                    <div>
                      <strong>{{ item.sku.product?.name || item.sku.sku_code }}</strong>
                      <span>{{ item.sku.specification }} · {{ item.sku.sku_code }}</span>
                    </div>
                    <div class="item-meta">
                      <strong>{{ money(item.sku.list_price) }}</strong>
                      <span>
                        计划 {{ item.planned_sales }} 件
                        · 到手价 {{ item.planned_unit_price ? money(item.planned_unit_price) : '未设置' }}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <div class="detail-panel">
                <div class="panel-title">准备包版本</div>
                <el-empty
                  v-if="!selectedSession.packages?.length" description="尚未创建准备包" :image-size="56"
                />
                <div v-else class="package-list">
                  <div v-for="pkg in [...selectedSession.packages].reverse()" :key="pkg.id" class="package-row">
                    <div><strong>版本 v{{ pkg.version }}</strong><span>{{ fmtDate(pkg.created_at) }}</span></div>
                    <el-tag :type="packageStatus[pkg.status]?.type || 'info'" effect="plain">
                      {{ packageStatus[pkg.status]?.text || pkg.status }}
                    </el-tag>
                  </div>
                </div>
              </div>
            </div>

            <section class="inspection-panel" v-loading="inspectionLoading">
              <div class="inspection-head">
                <div>
                  <div class="panel-title">直播前自动巡检</div>
                  <div class="inspection-sub">检查库存、活动、计划价格和商品资料，并保存每条规则的判断证据。</div>
                </div>
                <el-button
                  type="primary" :disabled="!selectedSession.items?.length"
                  :loading="inspectionLoading" @click="runInspection(selectedSession)"
                >立即巡检</el-button>
              </div>

              <el-empty
                v-if="!latestInspection" description="该场次还没有巡检记录"
                :image-size="56"
              />
              <template v-else>
                <div class="inspection-summary">
                  <div class="score" :class="latestInspection.conclusion">
                    <strong>{{ latestInspection.score }}</strong><span>准备分</span>
                  </div>
                  <div class="summary-item">
                    <strong>{{ latestInspection.conclusion === 'ready' ? '可开播' : '不可开播' }}</strong>
                    <span>巡检结论</span>
                  </div>
                  <div class="summary-item blocker">
                    <strong>{{ latestInspection.blocker_count }}</strong><span>阻断项</span>
                  </div>
                  <div class="summary-item warning">
                    <strong>{{ latestInspection.warning_count }}</strong><span>警告项</span>
                  </div>
                  <div class="summary-item passed">
                    <strong>{{ latestInspection.passed_count }}</strong><span>通过项</span>
                  </div>
                  <div class="summary-meta">
                    规则 {{ latestInspection.rule_version }} · {{ fmtDate(latestInspection.completed_at) }}
                  </div>
                </div>

                <el-alert
                  v-if="latestInspection.conclusion === 'ready'"
                  title="所有阻断规则均已通过，本场直播可以进入开播准备。"
                  type="success" :closable="false" show-icon
                />
                <el-table v-else :data="failedInspectionResults" size="small" class="inspection-table">
                  <el-table-column label="等级" width="80">
                    <template #default="{ row }">
                      <el-tag :type="row.severity === 'blocker' ? 'danger' : 'warning'" effect="plain">
                        {{ row.severity === 'blocker' ? '阻断' : '警告' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="rule_name" label="检查项" min-width="130" />
                  <el-table-column prop="message" label="发现的问题" min-width="260" show-overflow-tooltip />
                  <el-table-column prop="suggestion" label="处理建议" min-width="240" show-overflow-tooltip />
                </el-table>
              </template>
            </section>

            <section class="agent-panel" v-loading="agentLoading">
              <div class="inspection-head">
                <div>
                  <div class="panel-title"><el-icon><ChatDotRound /></el-icon> 智能运营建议</div>
                  <div class="inspection-sub">结合本场检查结果、审批状态和业务资料，解释风险并给出处理顺序。</div>
                </div>
                <el-button
                  type="primary" plain :icon="ChatDotRound"
                  :loading="agentLoading" @click="askSessionAgent()"
                >分析本场</el-button>
              </div>

              <div class="agent-questions">
                <el-input
                  v-model="agentQuestion" maxlength="1000" show-word-limit
                  placeholder="例如：本场为什么不能开播？先处理哪一项？"
                  @keyup.enter="askSessionAgent()"
                />
                <div class="quick-questions">
                  <el-button size="small" @click="askSessionAgent('为什么本场直播不能开播？')">解释阻断原因</el-button>
                  <el-button size="small" @click="askSessionAgent('请按优先级给出本场风险处理顺序。')">生成处理顺序</el-button>
                  <el-button size="small" @click="askSessionAgent('哪些处理动作需要审批？')">检查审批要求</el-button>
                </div>
              </div>

              <el-empty v-if="!agentResult" description="点击“分析本场”生成基于真实任务上下文的解释" :image-size="52" />
              <template v-else>
                <div class="agent-answer">{{ agentResult.answer }}</div>
                <div v-if="agentResult.recommended_actions?.length" class="agent-actions">
                  <div class="panel-title">结构化处理建议</div>
                  <div
                    v-for="(action, index) in agentResult.recommended_actions"
                    :key="`${action.rule_code}-${action.sku_id}-${index}`"
                    class="agent-action"
                  >
                    <el-tag :type="action.priority === 'high' ? 'danger' : 'warning'" effect="plain">
                      {{ action.priority === 'high' ? '优先处理' : '建议处理' }}
                    </el-tag>
                    <div>
                      <strong>{{ action.title }}</strong>
                      <span>{{ action.description }}</span>
                    </div>
                    <el-tag v-if="action.requires_approval" type="warning">需要审批</el-tag>
                    <el-button
                      v-if="action.requires_approval && action.expected_value && action.risk_id"
                      type="primary" plain size="small" :loading="approvalDraftLoading"
                      @click="openApprovalDraft(action)"
                    >生成审批草稿</el-button>
                  </div>
                </div>
                <div v-if="agentResult.sources?.length" class="agent-sources">
                  <strong>知识依据</strong>
                  <span v-for="source in agentResult.sources" :key="source.chunk_id">
                    [{{ source.index }}] {{ source.document_name }}
                  </span>
                </div>
                <div class="agent-meta">分析用时 {{ (agentResult.elapsed_ms / 1000).toFixed(1) }} 秒</div>
              </template>
            </section>
          </div>
        </el-tab-pane>

        <el-tab-pane label="基础数据" name="products">
          <div class="tab-toolbar">
            <span>维护景区门票、酒店套餐等旅游产品，以及成人票、儿童票、房型套餐等售卖规格。</span>
            <el-button v-if="user.isAdmin" type="primary" :icon="Plus" @click="openCreateProduct">
              新建旅游产品
            </el-button>
          </div>
          <el-empty v-if="!products.length" description="还没有商品" />
          <el-table v-else :data="products" row-key="id">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="sku-table">
                  <el-empty v-if="!row.skus?.length" description="该商品还没有 SKU" :image-size="50" />
                  <el-table v-else :data="row.skus" size="small">
                    <el-table-column prop="sku_code" label="SKU 编码" min-width="150" />
                    <el-table-column prop="specification" label="规格" min-width="150" />
                    <el-table-column label="标价" width="120">
                      <template #default="scope">{{ money(scope.row.list_price) }}</template>
                    </el-table-column>
                    <el-table-column prop="status" label="状态" width="100" />
                  </el-table>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="产品名称" min-width="220" />
            <el-table-column prop="brand" label="供应商/景区" min-width="130">
              <template #default="{ row }">{{ row.brand || '—' }}</template>
            </el-table-column>
            <el-table-column prop="category" label="产品类型" min-width="130">
              <template #default="{ row }">{{ row.category || '—' }}</template>
            </el-table-column>
            <el-table-column label="售卖规格" width="90">
              <template #default="{ row }">{{ row.skus?.length || 0 }}</template>
            </el-table-column>
            <el-table-column v-if="user.isAdmin" label="操作" width="120">
              <template #default="{ row }">
                <el-button link type="primary" @click="openCreateSku(row)">添加规格</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="平台数据快照" name="operations">
          <div class="operations-toolbar">
            <div class="price-form">
              <span>到手价试算</span>
              <el-select v-model="priceForm.sku_id" filterable placeholder="选择 SKU" style="width: 280px">
                <el-option v-for="sku in skuOptions" :key="sku.id" :label="sku.label" :value="sku.id" />
              </el-select>
              <el-input-number v-model="priceForm.quantity" :min="1" :max="100000" />
              <el-button type="primary" :loading="saving" @click="previewPrice">计算</el-button>
            </div>
            <div v-if="user.isAdmin" class="head-actions">
              <el-button @click="openInventory">录入库存</el-button>
              <el-button type="primary" @click="openPromotion">新建活动</el-button>
            </div>
          </div>

          <div v-if="priceResult" class="price-result">
            <div><span>原价合计</span><strong class="old-price">{{ money(priceResult.original_total) }}</strong></div>
            <div><span>计算后总价</span><strong>{{ money(priceResult.final_total) }}</strong></div>
            <div><span>优惠金额</span><strong class="saved">-{{ money(priceResult.saved_amount) }}</strong></div>
            <div><span>折算单价</span><strong>{{ money(priceResult.final_unit_price) }}</strong></div>
            <div class="applied-promotions">
              <span>采用活动</span>
              <template v-if="priceResult.applied_promotions?.length">
                <el-tag v-for="item in priceResult.applied_promotions" :key="item.promotion_id" effect="plain">
                  {{ item.name }}（-{{ money(item.amount) }}）
                </el-tag>
              </template>
              <span v-else class="muted">当前没有可用优惠</span>
            </div>
          </div>

          <div class="operation-grid">
            <section class="operation-panel">
              <div class="panel-title">最新可售库存</div>
              <el-empty v-if="!inventory.length" description="还没有库存数据" :image-size="56" />
              <el-table v-else :data="inventory" size="small">
                <el-table-column prop="sku.sku_code" label="SKU" min-width="130" />
                <el-table-column prop="sku.specification" label="规格" min-width="100" />
                <el-table-column prop="available_quantity" label="可用" width="70" />
                <el-table-column prop="reserved_quantity" label="锁定" width="70" />
                <el-table-column label="可售" width="70">
                  <template #default="{ row }"><strong>{{ row.sellable_quantity }}</strong></template>
                </el-table-column>
                <el-table-column label="更新时间" min-width="155">
                  <template #default="{ row }">{{ fmtDate(row.captured_at) }}</template>
                </el-table-column>
              </el-table>
            </section>

            <section class="operation-panel">
              <div class="panel-title">直播优惠活动</div>
              <el-empty v-if="!promotions.length" description="还没有促销活动" :image-size="56" />
              <el-table v-else :data="promotions" size="small">
                <el-table-column prop="name" label="活动" min-width="150" show-overflow-tooltip />
                <el-table-column label="规则" min-width="150">
                  <template #default="{ row }">{{ promotionRule(row) }}</template>
                </el-table-column>
                <el-table-column label="适用 SKU" min-width="130">
                  <template #default="{ row }">{{ row.sku_links.map((x) => x.sku.sku_code).join('、') }}</template>
                </el-table-column>
                <el-table-column label="叠加" width="70">
                  <template #default="{ row }">{{ row.stackable ? '允许' : '不可' }}</template>
                </el-table-column>
                <el-table-column label="有效期" min-width="170">
                  <template #default="{ row }">至 {{ fmtDate(row.ends_at) }}</template>
                </el-table-column>
              </el-table>
            </section>
          </div>
        </el-tab-pane>

        <el-tab-pane label="巡检规则中心" name="rules">
          <div class="tab-toolbar">
            <span>规则由系统执行确定性判断，管理员可调整启停、风险等级和安全参数；每次修改都会产生新版本。</span>
          </div>
          <el-table :data="inspectionRules" row-key="rule_code">
            <el-table-column prop="rule_name" label="规则与业务含义" min-width="250">
              <template #default="{ row }">
                <strong>{{ row.rule_name }}</strong>
                <div class="rule-desc">{{ row.description }}</div>
                <div class="rule-code">{{ row.rule_code }}</div>
              </template>
            </el-table-column>
            <el-table-column label="风险等级" width="130">
              <template #default="{ row }">
                <el-select
                  v-if="user.isAdmin" :model-value="row.severity" size="small"
                  @change="(value) => updateRuleSeverity(row, value)"
                >
                  <el-option label="阻断" value="blocker" />
                  <el-option label="警告" value="warning" />
                  <el-option label="提示" value="info" />
                </el-select>
                <el-tag v-else :type="ruleSeverityType[row.severity] || 'info'">
                  {{ ruleSeverityText[row.severity] || row.severity }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="规则参数" min-width="180">
              <template #default="{ row }">
                <div v-if="row.rule_code === 'INVENTORY_FRESH'" class="rule-param">
                  <span>最大时效</span>
                  <el-input-number
                    :model-value="row.parameters.max_age_hours" :min="1" :max="168"
                    :disabled="!user.isAdmin" size="small"
                    @change="(value) => updateInventoryAge(row, value)"
                  />
                  <span>小时</span>
                </div>
                <span v-else class="muted">无可配置参数</span>
              </template>
            </el-table-column>
            <el-table-column prop="version" label="版本" width="80">
              <template #default="{ row }">v{{ row.version }}</template>
            </el-table-column>
            <el-table-column label="启用" width="90" fixed="right">
              <template #default="{ row }">
                <el-switch
                  :model-value="row.enabled" :disabled="!user.isAdmin"
                  @change="(value) => updateRuleEnabled(row, value)"
                />
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="recapOpen" title="单场直播复盘" width="920px" top="5vh">
      <div v-loading="recapLoading" class="recap-body">
        <template v-if="recap">
          <el-alert
            :title="recap.headline"
            :type="recap.readiness === 'ready' ? 'success' : recap.readiness === 'blocked' ? 'error' : 'warning'"
            :closable="false" show-icon
          />
          <div class="recap-metrics">
            <div><span>选品 SKU</span><strong>{{ recap.sku_count }}</strong></div>
            <div><span>计划销量</span><strong>{{ recap.planned_sales }}</strong></div>
            <div><span>计划 GMV</span><strong>{{ money(recap.planned_gmv) }}</strong></div>
            <div><span>最新准备分</span><strong>{{ recap.inspection.latest?.score ?? '—' }}</strong></div>
            <div><span>自动处理成功率</span><strong>{{ metric(recap.automation.success_rate, '%') }}</strong></div>
            <div><span>平均耗时</span><strong>{{ metric(recap.automation.avg_duration_seconds, ' 秒') }}</strong></div>
          </div>

          <el-tabs>
            <el-tab-pane label="选品与风险">
              <h4>本场选品</h4>
              <el-table :data="recap.items" size="small" border>
                <el-table-column prop="sku_code" label="SKU" width="130" />
                <el-table-column prop="product_name" label="商品" min-width="150" />
                <el-table-column prop="specification" label="规格" min-width="100" />
                <el-table-column prop="planned_sales" label="计划销量" width="90" />
                <el-table-column label="计划单价" width="110">
                  <template #default="{ row }">{{ row.planned_unit_price ? money(row.planned_unit_price) : '—' }}</template>
                </el-table-column>
                <el-table-column label="计划 GMV" width="120">
                  <template #default="{ row }">{{ row.planned_gmv ? money(row.planned_gmv) : '—' }}</template>
                </el-table-column>
              </el-table>
              <h4>最新巡检未通过项</h4>
              <el-empty v-if="!recap.inspection.unresolved_risks.length" description="没有未通过风险" :image-size="48" />
              <el-table v-else :data="recap.inspection.unresolved_risks" size="small" border>
                <el-table-column label="等级" width="80">
                  <template #default="{ row }">
                    <el-tag :type="row.severity === 'blocker' ? 'danger' : 'warning'" size="small">
                      {{ row.severity === 'blocker' ? '阻断' : '警告' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="rule_name" label="检查项" min-width="130" />
                <el-table-column prop="message" label="问题" min-width="240" />
                <el-table-column prop="suggestion" label="处理建议" min-width="220" />
              </el-table>
            </el-tab-pane>
            <el-tab-pane :label="`自动化任务（${recap.automation.total}）`">
              <el-table :data="recap.automation.tasks" size="small" border>
                <el-table-column prop="id" label="#" width="60" />
                <el-table-column label="类型" min-width="130">
                  <template #default="{ row }">{{ taskTypeLabel(row.task_type) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="100">
                  <template #default="{ row }">{{ taskStatusLabel(row.status) }}</template>
                </el-table-column>
                <el-table-column prop="attempt_count" label="尝试" width="70" />
                <el-table-column label="耗时" width="100">
                  <template #default="{ row }">{{ metric(row.duration_seconds, ' 秒') }}</template>
                </el-table-column>
                <el-table-column prop="error_message" label="错误" min-width="220">
                  <template #default="{ row }">{{ row.error_message || '—' }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane :label="`审批闭环（${recap.approvals.total}）`">
              <el-table :data="recap.approvals.approvals" size="small" border>
                <el-table-column prop="id" label="#" width="60" />
                <el-table-column label="操作" width="120">
                  <template #default="{ row }">{{ taskTypeLabel(row.task_type) }}</template>
                </el-table-column>
                <el-table-column prop="sku_code" label="SKU" min-width="130" />
                <el-table-column label="状态" min-width="120">
                  <template #default="{ row }">{{ approvalStatusLabel(row.status) }}</template>
                </el-table-column>
                <el-table-column label="决策耗时" width="110">
                  <template #default="{ row }">{{ metric(row.decision_seconds, ' 秒') }}</template>
                </el-table-column>
                <el-table-column label="关联任务" width="100">
                  <template #default="{ row }">{{ row.task_id ? `#${row.task_id}` : '—' }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>
          <el-alert
            title="当前尚未接入真实订单与退款数据，本报告不展示成交额、退款率等结果指标。"
            type="info" :closable="false" show-icon
          />
        </template>
      </div>
      <template #footer>
        <el-button @click="recapOpen = false">关闭</el-button>
        <el-button type="primary" :icon="Download" :disabled="!recap" @click="exportRecap">
          导出 Markdown
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="approvalDraftOpen" title="确认智能建议生成的审批草稿" width="600px">
      <template v-if="approvalDraft">
        <el-alert
          v-if="!approvalDraft.can_submit"
          :title="`该风险已有未结审批 #${approvalDraft.existing_approval_id}`"
          type="warning" :closable="false" show-icon
        />
        <el-descriptions :column="2" border class="approval-draft-summary">
          <el-descriptions-item label="直播场次">#{{ approvalDraft.session_id }}</el-descriptions-item>
          <el-descriptions-item label="来源巡检">#{{ approvalDraft.inspection_id }}</el-descriptions-item>
          <el-descriptions-item label="风险证据">#{{ approvalDraft.source_inspection_result_id }}</el-descriptions-item>
          <el-descriptions-item label="目标 SKU">{{ approvalDraft.sku_code }}</el-descriptions-item>
          <el-descriptions-item label="执行动作">修改直播价</el-descriptions-item>
          <el-descriptions-item label="目标价格">¥{{ approvalDraft.expected_value.live_price }}</el-descriptions-item>
        </el-descriptions>
        <el-form label-position="top">
          <el-form-item label="巡检证据">
            <div class="draft-evidence">
              平台计算价：¥{{ approvalDraft.evidence.calculated_unit_price || '—' }}；
              计划价：¥{{ approvalDraft.evidence.planned_unit_price || '—' }}
            </div>
          </el-form-item>
          <el-form-item label="申请理由">
            <el-input
              v-model="approvalDraftReason" type="textarea" :rows="3"
              maxlength="512" show-word-limit
            />
          </el-form-item>
        </el-form>
        <el-alert
          title="确认后只提交审批，不会立即修改平台；负责人审批通过后才生成自动化任务。"
          type="info" :closable="false" show-icon
        />
      </template>
      <template #footer>
        <el-button @click="approvalDraftOpen = false">取消</el-button>
        <el-button v-if="approvalDraft && !approvalDraft.can_submit" type="primary" @click="goApprovals">
          查看已有审批
        </el-button>
        <el-button
          v-else type="primary" :loading="approvalDraftLoading"
          @click="submitApprovalDraft"
        >确认提交审批</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="sessionDialog" title="创建直播场次" width="480px">
      <el-form label-position="top">
        <el-form-item label="场次名称" required>
          <el-input v-model="sessionForm.title" maxlength="128" placeholder="例如：周末亲子景区门票专场" />
        </el-form-item>
        <el-form-item label="计划开播时间" required>
          <el-date-picker
            v-model="sessionForm.starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="选择时间" style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sessionDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitSession">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="productDialog" title="新建旅游产品" width="480px">
      <el-form label-position="top">
        <el-form-item label="产品名称" required><el-input v-model="productForm.name" maxlength="128" placeholder="例如：欢乐谷成人全天票" /></el-form-item>
        <el-form-item label="供应商/景区"><el-input v-model="productForm.brand" maxlength="64" placeholder="例如：欢乐谷景区" /></el-form-item>
        <el-form-item label="产品类型"><el-input v-model="productForm.category" maxlength="64" placeholder="例如：景区门票、酒店套餐" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="productDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitProduct">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="skuDialog" :title="`为「${skuProduct?.name || ''}」添加 SKU`" width="520px">
      <el-form label-position="top">
        <el-form-item label="SKU 编码" required>
          <el-input v-model="skuForm.sku_code" maxlength="64" placeholder="例如 YL-SH-450" />
        </el-form-item>
        <el-form-item label="规格" required>
          <el-input v-model="skuForm.specification" maxlength="128" placeholder="例如 成人票、双人票、周末大床房" />
        </el-form-item>
        <el-form-item label="日常售价" required>
          <el-input-number v-model="skuForm.list_price" :min="0.01" :precision="2" :step="1" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="skuDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitSku">添加</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="itemDialog" :title="`为「${itemSession?.title || ''}」添加选品`" width="540px">
      <el-form label-position="top">
        <el-form-item label="商品 SKU" required>
          <el-select v-model="itemForm.sku_id" filterable placeholder="选择 SKU" style="width: 100%">
            <el-option v-for="sku in skuOptions" :key="sku.id" :label="sku.label" :value="sku.id" />
          </el-select>
          <div v-if="!skuOptions.length" class="form-hint">暂无可用售卖规格，请先在“基础数据”中创建旅游产品和规格。</div>
        </el-form-item>
        <el-form-item label="计划销量">
          <el-input-number v-model="itemForm.planned_sales" :min="0" :step="10" style="width: 100%" />
        </el-form-item>
        <el-form-item label="计划到手单价">
          <el-input-number
            v-model="itemForm.planned_unit_price" :min="0.01" :precision="2" :step="1"
            placeholder="用于校验平台活动后的实际到手价" style="width: 100%"
          />
          <div class="form-hint">不填写不会直接阻断开播，但巡检会提示补充价格计划。</div>
        </el-form-item>
        <el-form-item label="讲解顺序">
          <el-input-number v-model="itemForm.sort_order" :min="0" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="itemDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!skuOptions.length" @click="submitItem">
          添加
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="inventoryDialog" title="录入库存快照" width="520px">
      <el-form label-position="top">
        <el-form-item label="商品 SKU" required>
          <el-select v-model="inventoryForm.sku_id" filterable placeholder="选择 SKU" style="width: 100%">
            <el-option v-for="sku in skuOptions" :key="sku.id" :label="sku.label" :value="sku.id" />
          </el-select>
        </el-form-item>
        <div class="dialog-grid">
          <el-form-item label="可用库存" required>
            <el-input-number v-model="inventoryForm.available_quantity" :min="0" style="width: 100%" />
          </el-form-item>
          <el-form-item label="锁定库存">
            <el-input-number v-model="inventoryForm.reserved_quantity" :min="0" style="width: 100%" />
          </el-form-item>
        </div>
        <el-form-item label="数据来源">
          <el-input v-model="inventoryForm.source" maxlength="32" placeholder="例如 供应商系统、票务系统" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="inventoryDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitInventory">保存快照</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="promotionDialog" title="新建直播优惠活动" width="640px">
      <el-form label-position="top">
        <div class="dialog-grid">
          <el-form-item label="活动名称" required>
            <el-input v-model="promotionForm.name" maxlength="128" placeholder="例如 周末亲子专场直播价" />
          </el-form-item>
          <el-form-item label="活动类型" required>
            <el-select v-model="promotionForm.promotion_type" style="width: 100%">
              <el-option v-for="(label, value) in promotionTypes" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="适用 SKU" required>
          <el-select
            v-model="promotionForm.sku_ids" multiple filterable collapse-tags
            placeholder="可选择多个 SKU" style="width: 100%"
          >
            <el-option v-for="sku in skuOptions" :key="sku.id" :label="sku.label" :value="sku.id" />
          </el-select>
        </el-form-item>
        <div v-if="promotionForm.promotion_type === 'live_price'" class="dialog-grid">
          <el-form-item label="直播固定价" required>
            <el-input-number
              v-model="promotionForm.fixed_price" :min="0.01" :precision="2" style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="叠加其他活动">
            <el-switch v-model="promotionForm.stackable" active-text="允许叠加" inactive-text="不可叠加" />
          </el-form-item>
        </div>
        <div v-else class="dialog-grid">
          <el-form-item label="使用门槛">
            <el-input-number
              v-model="promotionForm.threshold_amount" :min="0" :precision="2" style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="优惠金额" required>
            <el-input-number
              v-model="promotionForm.discount_amount" :min="0.01" :precision="2" style="width: 100%"
            />
          </el-form-item>
        </div>
        <el-form-item v-if="promotionForm.promotion_type !== 'live_price'" label="叠加其他活动">
          <el-switch v-model="promotionForm.stackable" active-text="允许叠加" inactive-text="不可叠加" />
        </el-form-item>
        <div class="dialog-grid">
          <el-form-item label="开始时间" required>
            <el-date-picker
              v-model="promotionForm.starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="结束时间" required>
            <el-date-picker
              v-model="promotionForm.ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss"
              style="width: 100%"
            />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="promotionDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitPromotion">创建活动</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.commerce { display: flex; flex-direction: column; gap: 14px; }
.hero {
  display: flex; justify-content: space-between; align-items: center; gap: 20px;
  padding: 22px 24px; border-radius: 12px;
  color: #fff; background: linear-gradient(125deg, #1f2d3d 0%, #263f58 55%, #35607f 100%);
}
.hero h1 { margin: 4px 0 6px; font-size: 24px; }
.hero p { margin: 0; color: rgba(255,255,255,.72); font-size: 13px; }
.eyebrow { font-size: 10px; letter-spacing: 1.6px; color: #7dd3fc; }
.hero-actions { display: flex; gap: 8px; flex: none; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.metric-card {
  display: flex; align-items: center; gap: 14px; padding: 15px 18px;
  background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 10px;
}
.metric-card > .el-icon {
  width: 38px; height: 38px; border-radius: 8px; font-size: 20px;
  color: var(--el-color-primary); background: var(--el-color-primary-light-9);
}
.metric-card div { display: flex; flex-direction: column; gap: 2px; }
.metric-card strong { font-size: 22px; line-height: 1.2; }
.metric-card span, .tab-toolbar > span { color: var(--el-text-color-secondary); font-size: 12px; }
.workspace { min-height: 360px; }
.tab-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.session-detail { margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.detail-head h3 { margin: 0 0 4px; font-size: 16px; }
.detail-head span { color: var(--el-text-color-secondary); font-size: 12px; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.detail-panel { padding: 14px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.panel-title { margin-bottom: 10px; font-size: 13px; font-weight: 600; }
.item-list, .package-list { display: flex; flex-direction: column; gap: 8px; }
.item-row, .package-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 10px 12px; background: var(--el-fill-color-light); border-radius: 6px;
}
.item-row > div, .package-row > div { display: flex; flex-direction: column; gap: 3px; }
.item-row span, .package-row span { color: var(--el-text-color-secondary); font-size: 11px; }
.item-meta { text-align: right; flex: none; }
.inspection-panel {
  margin-top: 14px; padding: 16px; border: 1px solid var(--el-border-color-light);
  border-radius: 9px; background: var(--el-fill-color-blank);
}
.inspection-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.inspection-sub { color: var(--el-text-color-secondary); font-size: 12px; }
.inspection-summary {
  display: flex; align-items: center; gap: 24px; margin: 16px 0;
  padding: 14px; border-radius: 8px; background: var(--el-fill-color-light);
}
.score {
  display: grid; place-items: center; width: 68px; height: 68px; flex: none;
  border: 5px solid var(--el-color-danger-light-5); border-radius: 50%;
}
.score.ready { border-color: var(--el-color-success-light-5); }
.score strong, .score span, .summary-item strong, .summary-item span { display: block; }
.score strong { font-size: 23px; line-height: 1; }
.score span, .summary-item span { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 11px; }
.summary-item strong { font-size: 17px; }
.summary-item.blocker strong { color: var(--el-color-danger); }
.summary-item.warning strong { color: var(--el-color-warning); }
.summary-item.passed strong { color: var(--el-color-success); }
.summary-meta { margin-left: auto; color: var(--el-text-color-secondary); font-size: 11px; }
.inspection-table { margin-top: 12px; }
.recap-body { min-height: 220px; }
.recap-metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0;
}
.recap-metrics > div {
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  text-align: center;
}
.recap-metrics span { display: block; color: var(--el-text-color-secondary); font-size: 12px; }
.recap-metrics strong { display: block; margin-top: 5px; font-size: 18px; }
.agent-panel {
  margin-top: 16px; padding: 18px; border: 1px solid var(--el-color-primary-light-7);
  border-radius: 10px; background: var(--el-color-primary-light-9);
}
.agent-panel .panel-title { display: flex; align-items: center; gap: 6px; }
.agent-questions { display: flex; flex-direction: column; gap: 9px; margin: 14px 0; }
.quick-questions { display: flex; flex-wrap: wrap; gap: 6px; }
.agent-answer {
  padding: 14px; border-radius: 8px; background: var(--el-bg-color); line-height: 1.75;
  white-space: pre-wrap; border-left: 3px solid var(--el-color-primary);
}
.agent-actions { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
.agent-action {
  display: grid; grid-template-columns: 78px minmax(0, 1fr) auto; align-items: start;
  gap: 10px; padding: 10px; border-radius: 8px; background: var(--el-bg-color);
}
.agent-action strong, .agent-action span { display: block; }
.agent-action span { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; }
.approval-draft-summary { margin: 14px 0; }
.draft-evidence {
  width: 100%; padding: 10px 12px; border-radius: 6px;
  color: var(--el-text-color-regular); background: var(--el-fill-color-light);
}
.agent-sources { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; font-size: 12px; }
.agent-sources span { color: var(--el-color-primary); }
.agent-meta { margin-top: 10px; color: var(--el-text-color-secondary); font-size: 11px; }
.sku-table { padding: 8px 48px 12px; }
.form-hint { margin-top: 6px; color: var(--el-color-warning); font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.rule-code { margin-top: 3px; color: var(--el-text-color-secondary); font-size: 11px; font-family: monospace; }
.rule-desc { margin-top: 4px; color: var(--el-text-color-regular); font-size: 12px; line-height: 1.5; }
.rule-param { display: flex; align-items: center; gap: 7px; font-size: 12px; }
.rule-param :deep(.el-input-number) { width: 100px; }
.operations-toolbar {
  display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 14px;
}
.price-form { display: flex; align-items: center; gap: 10px; }
.price-form > span { font-size: 13px; font-weight: 600; white-space: nowrap; }
.price-result {
  display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px;
  padding: 14px; margin-bottom: 14px; border: 1px solid var(--el-color-success-light-5);
  border-radius: 8px; background: var(--el-color-success-light-9);
}
.price-result > div { display: flex; flex-direction: column; gap: 4px; }
.price-result span { color: var(--el-text-color-secondary); font-size: 11px; }
.price-result strong { font-size: 19px; }
.price-result .old-price { color: var(--el-text-color-secondary); text-decoration: line-through; }
.price-result .saved { color: var(--el-color-success); }
.price-result .applied-promotions {
  grid-column: 1 / -1; flex-direction: row; align-items: center; flex-wrap: wrap;
  padding-top: 10px; border-top: 1px solid var(--el-color-success-light-7);
}
.operation-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.operation-panel { min-width: 0; padding: 14px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.dialog-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) {
  .hero { align-items: flex-start; flex-direction: column; }
  .detail-grid, .operation-grid, .price-result { grid-template-columns: 1fr; }
  .inspection-summary { align-items: flex-start; flex-wrap: wrap; }
  .recap-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-meta { width: 100%; margin-left: 0; }
  .operations-toolbar, .price-form { align-items: stretch; flex-direction: column; }
  .price-result .applied-promotions { grid-column: auto; }
  .dialog-grid { grid-template-columns: 1fr; gap: 0; }
}
@media (max-width: 560px) {
  .metrics { grid-template-columns: 1fr; }
}
</style>
