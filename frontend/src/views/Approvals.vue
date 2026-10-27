<template>
  <div class="approvals">
    <el-card>
      <template #header>
        <div class="head">
          <div>
            <span>风险操作审批</span>
            <span class="hint">改价和下架需要负责人复核，通过后由系统自动执行并核验结果</span>
          </div>
          <div>
            <el-button :loading="loading" @click="load">刷新</el-button>
            <el-button type="primary" @click="openCreate">发起申请</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="场次" width="90">
          <template #default="{ row }">#{{ row.session_id }}</template>
        </el-table-column>
        <el-table-column label="风险来源" width="110">
          <template #default="{ row }">
            <span v-if="row.source_inspection_result_id">巡检风险 #{{ row.source_inspection_result_id }}</span>
            <span v-else class="muted">人工申请</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.task_type === 'price_fix' ? 'warning' : 'danger'">
              {{ row.task_type === 'price_fix' ? '改价' : '下架' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="目标 SKU" width="130">
          <template #default="{ row }">{{ row.sku_code || row.sku_id }}</template>
        </el-table-column>
        <el-table-column label="预期值" min-width="150">
          <template #default="{ row }">
            <span v-if="row.expected_value.live_price">直播价 {{ row.expected_value.live_price }}</span>
            <span v-else-if="row.expected_value.platform_status">状态 {{ row.expected_value.platform_status }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="理由" min-width="160">
          <template #default="{ row }">
            <span class="msg">{{ row.reason || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="执行任务" width="90">
          <template #default="{ row }">
            <router-link v-if="row.task_id" to="/automation" class="link">#{{ row.task_id }}</router-link>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'pending'"
              link type="success" @click="decide(row, true)"
            >通过</el-button>
            <el-button
              v-if="row.status === 'pending'"
              link type="danger" @click="decide(row, false)"
            >驳回</el-button>
            <span v-if="row.status !== 'pending'" class="muted">
              {{ row.decision_note || '已处理' }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !rows.length" description="没有审批申请" />
    </el-card>

    <!-- 发起申请 -->
    <el-dialog v-model="createOpen" title="发起高风险操作申请" width="520px">
      <el-form label-width="92px">
        <el-form-item label="场次">
          <el-select v-model="form.session_id" placeholder="选择直播场次" style="width: 100%" @change="loadItems">
            <el-option v-for="s in sessions" :key="s.id" :label="`#${s.id} ${s.title}`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标 SKU">
          <el-select v-model="form.sku_id" placeholder="本场选品" style="width: 100%" :disabled="!form.session_id">
            <el-option v-for="it in items" :key="it.sku_id" :label="it.sku_code || `SKU ${it.sku_id}`" :value="it.sku_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="操作类型">
          <el-radio-group v-model="form.task_type" @change="form.value = ''">
            <el-radio value="price_fix">修改直播价</el-radio>
            <el-radio value="delist">商品下架</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.task_type === 'price_fix'" label="目标直播价">
          <el-input v-model="form.value" placeholder="如 99.00" style="width: 180px" />
        </el-form-item>
        <el-form-item label="申请理由">
          <el-input v-model="form.reason" type="textarea" :rows="2" maxlength="512" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">提交申请</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { commerceApi, rpaApi } from '../api'

/**
 * 风险与审批：高风险操作（改价 / 下架）的申请与审批。
 *
 * 两个刻意的产品决定：
 *  · **申请与审批同页**，但"通过/驳回"的权限在后端判定 —— 申请人不能批自己的单
 *    （四眼原则），所以同一个按钮对申请人是会报 40300 的；前端不做按钮级隐藏，
 *    是因为"谁有审批权"取决于场次归属（动态），前端算不准，交给后端给结论。
 *  · 通过后生成的是**执行任务**，不是直接改价 —— 到「自动化任务」看步骤与二次验证结果。
 */

const rows = ref([])
const loading = ref(false)
const sessions = ref([])
const items = ref([])
const createOpen = ref(false)
const saving = ref(false)
const form = reactive({ session_id: null, sku_id: null, task_type: 'price_fix', value: '', reason: '' })

const STATUS = {
  pending: { label: '待审批', tag: 'warning' },
  approved: { label: '已通过', tag: 'success' },
  rejected: { label: '已驳回', tag: 'info' },
  executed: { label: '已执行', tag: 'success' },
  failed: { label: '执行失败', tag: 'danger' },
}
function statusLabel(s) {
  return STATUS[s]?.label || s
}
function statusTag(s) {
  return STATUS[s]?.tag || 'info'
}

async function load() {
  loading.value = true
  try {
    rows.value = (await rpaApi.approvals()).data || []
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function openCreate() {
  createOpen.value = true
  form.session_id = null
  form.sku_id = null
  form.task_type = 'price_fix'
  form.value = ''
  form.reason = ''
  try {
    sessions.value = (await commerceApi.sessions()).data || []
  } catch {
    /* 拦截器已提示 */
  }
}

async function loadItems() {
  form.sku_id = null
  items.value = []
  if (!form.session_id) return
  try {
    const detail = (await commerceApi.session(form.session_id)).data
    items.value = detail.items || []
  } catch {
    /* 拦截器已提示 */
  }
}

async function submit() {
  if (!form.session_id || !form.sku_id) {
    ElMessage.warning('请选择场次与 SKU')
    return
  }
  const expected =
    form.task_type === 'price_fix'
      ? { live_price: String(form.value || '').trim() }
      : { platform_status: 'inactive' }
  if (form.task_type === 'price_fix' && !expected.live_price) {
    ElMessage.warning('请填写目标直播价')
    return
  }
  saving.value = true
  try {
    await rpaApi.createApproval({
      session_id: form.session_id,
      task_type: form.task_type,
      sku_id: form.sku_id,
      expected_value: expected,
      reason: form.reason || null,
    })
    ElMessage.success('申请已提交，等待负责人审批')
    createOpen.value = false
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function decide(row, approve) {
  let note = null
  try {
    const res = await ElMessageBox.prompt(
      approve ? '可填写审批备注（可留空）' : '驳回理由',
      approve ? '通过审批' : '驳回申请',
      { inputPlaceholder: '可留空', inputValue: '' },
    )
    note = res.value || null
  } catch {
    return // 用户取消
  }
  try {
    const body = await rpaApi.decideApproval(row.id, { approve, note })
    ElMessage.success(body.message || '已处理')
    await load()
  } catch {
    /* 拦截器已提示（含"申请人不能审批自己的申请"） */
  }
}

onMounted(load)
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.msg {
  font-size: 12px;
}
.link {
  color: var(--el-color-primary);
}
</style>
