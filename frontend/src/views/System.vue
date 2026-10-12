<script setup>
/**
 * 系统状态页（仅系统管理员）。
 *
 * 三块：依赖连通性（MySQL / Milvus / Redis）、向量库自检、最近管理动作。
 *
 * 「向量库自检」这块和知识库页的那张卡片**共用同一个接口**，但视角不同：
 * 那边嵌在"库"的上下文里（便于看到孤儿就顺手去处理），这里是系统级巡检。
 * 重复一个入口是刻意的 —— 排障时人第一个会来的地方是"系统状态"，不是"知识库"。
 */
import { computed, onMounted, ref } from 'vue'

import { auditApi, systemApi, vectorApi } from '../api'

const healthLoading = ref(false)
const health = ref(null)

const vectorLoading = ref(false)
const vectorHealth = ref(null)

const auditLoading = ref(false)
const auditLogs = ref([])
const auditHasMore = ref(false)
const auditAction = ref('')      // '' = 全部
const auditActions = ref([])

const DEPS = [
  { key: 'mysql', label: 'MySQL', note: '业务数据（知识库 / 文档 / 会话）' },
  { key: 'milvus', label: 'Milvus', note: '向量库（检索用）' },
  { key: 'redis', label: 'Redis', note: '限流计数（不可用时限流降级为放行）' },
]

const allOk = computed(() => {
  if (!health.value) return false
  return DEPS.every((d) => health.value[d.key] === 'ok')
})

async function loadHealth() {
  healthLoading.value = true
  try {
    const body = await systemApi.health()
    health.value = body.data
  } catch {
    /* 拦截器已提示（后端没起时这里也会失败，下方有兜底文案） */
  } finally {
    healthLoading.value = false
  }
}

async function loadVector() {
  vectorLoading.value = true
  try {
    const body = await vectorApi.health()
    vectorHealth.value = body.data
  } catch {
    /* 忽略 */
  } finally {
    vectorLoading.value = false
  }
}

const AUDIT_PAGE_SIZE = 20

async function loadAudit() {
  auditLoading.value = true
  try {
    const params = { limit: AUDIT_PAGE_SIZE }
    if (auditAction.value) params.action = auditAction.value
    const body = await auditApi.list(params)
    auditLogs.value = body.data?.items || []
    auditHasMore.value = !!body.data?.has_more
  } catch {
    /* 忽略 */
  } finally {
    auditLoading.value = false
  }
}

/** 加载更多：keyset，传当前最旧那条的 id —— 不是 offset。 */
async function loadMoreAudit() {
  if (!auditLogs.value.length) return
  auditLoading.value = true
  try {
    const params = { limit: AUDIT_PAGE_SIZE, before_id: auditLogs.value[auditLogs.value.length - 1].id }
    if (auditAction.value) params.action = auditAction.value
    const body = await auditApi.list(params)
    auditLogs.value = [...auditLogs.value, ...(body.data?.items || [])]
    auditHasMore.value = !!body.data?.has_more
  } catch {
    /* 忽略 */
  } finally {
    auditLoading.value = false
  }
}

async function loadAuditActions() {
  try {
    const body = await auditApi.actions()
    auditActions.value = body.data || []
  } catch {
    /* 忽略：下拉空着也能用 */
  }
}

function loadAll() {
  loadHealth()
  loadVector()
  loadAuditActions()
  loadAudit()
}

/** 把审计的 detail 渲染成一句话，比让用户看 JSON 强 */
function detailText(row) {
  const d = row.detail || {}
  switch (row.action) {
    case 'user.role_change':
      return `角色 ${d.from} → ${d.to}`
    case 'user.create':
      return `开通为 ${d.role_code}（${d.email}）`
    case 'user.status_change':
      return d.to === 'active' ? '已启用该账号' : '已停用该账号'
    case 'user.password_reset':
      return '重置了该用户的密码（旧密码与登录态已失效）'
    case 'kb.member_grant':
      return d.previous
        ? `把 ${d.username} 的权限从 ${d.previous} 改为 ${d.permission}`
        : `授权 ${d.username}（${d.permission}）`
    case 'kb.member_revoke':
      return `移除 ${d.username}（原权限 ${d.previous}）`
    case 'kb.create':
      return d.is_public ? '创建了公开知识库' : '创建了私有知识库'
    case 'kb.delete':
      return '删除了该知识库'
    case 'kb.visibility_change':
      return `可见性 ${d.from === 'public' ? '公开' : '私有'} → ${d.to === 'public' ? '公开' : '私有'}`
    default:
      return JSON.stringify(d)
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="sys">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>
            依赖连通性
            <el-tag v-if="health" :type="allOk ? 'success' : 'danger'" size="small" class="head-tag">
              {{ allOk ? '全部正常' : '存在异常' }}
            </el-tag>
          </span>
          <el-button size="small" :loading="healthLoading" @click="loadAll">全部刷新</el-button>
        </div>
      </template>

      <el-empty v-if="!health && !healthLoading" description="取不到健康状态（后端没起？）" :image-size="60" />
      <div v-else class="deps">
        <div v-for="d in DEPS" :key="d.key" class="dep" :class="{ bad: health && health[d.key] !== 'ok' }">
          <div class="dep-top">
            <span class="dep-name">{{ d.label }}</span>
            <el-tag size="small" :type="health?.[d.key] === 'ok' ? 'success' : 'danger'">
              {{ health?.[d.key] === 'ok' ? '正常' : '异常' }}
            </el-tag>
          </div>
          <div class="dep-note">{{ d.note }}</div>
        </div>
      </div>
      <div class="hint">
        健康检查结果有 3 秒缓存 —— 短时间内连点不会反复打外部依赖。
      </div>
    </el-card>

    <el-card v-loading="vectorLoading" shadow="never">
      <template #header>
        <div class="card-head">
          <span>向量库</span>
          <el-button size="small" :loading="vectorLoading" @click="loadVector">自检</el-button>
        </div>
      </template>
      <template v-if="vectorHealth">
        <div v-if="!vectorHealth.available" class="down">
          <el-tag type="danger" size="small">不可用</el-tag>
          <span>{{ vectorHealth.reason }}</span>
        </div>
        <template v-else>
          <div class="vector-grid">
            <div class="vector-item">
              <span class="vnum">{{ vectorHealth.vector_count }}</span>
              <span class="vlabel">向量总数</span>
            </div>
            <div class="vector-item" :class="{ warn: vectorHealth.orphan_vector_count }">
              <span class="vnum">{{ vectorHealth.orphan_vector_count ?? 0 }}</span>
              <span class="vlabel">孤儿向量</span>
            </div>
            <div class="vector-item" :class="{ warn: vectorHealth.documents_without_vectors }">
              <span class="vnum">{{ vectorHealth.documents_without_vectors ?? 0 }}</span>
              <span class="vlabel">缺向量文档</span>
            </div>
          </div>
          <div class="hint">
            Milvus {{ vectorHealth.server_version }} · collection
            <code>{{ vectorHealth.collection }}</code>
            <template v-if="!vectorHealth.collection_exists">（尚未创建）</template>
            <br>
            清理孤儿向量请用<a href="/devtools/" target="_blank" rel="noopener">应急调试台</a>
            （真删数据的操作刻意不放在主界面）。
          </div>
        </template>
      </template>
      <el-empty v-else description="尚未自检" :image-size="60" />
    </el-card>

    <el-card v-loading="auditLoading" shadow="never">
      <template #header>
        <div class="card-head">
          <span>最近管理动作</span>
          <div class="head-actions">
            <el-select
              v-model="auditAction"
              placeholder="全部动作"
              clearable
              size="small"
              style="width: 150px"
              @change="loadAudit"
            >
              <el-option v-for="a in auditActions" :key="a.action" :label="a.text" :value="a.action" />
            </el-select>
            <el-button size="small" :loading="auditLoading" @click="loadAudit">刷新</el-button>
          </div>
        </div>
      </template>
      <el-empty
        v-if="!auditLogs.length"
        description="还没有管理动作（改角色 / 授权 / 改可见性 / 建库 / 删库会记在这里）"
        :image-size="60"
      />
      <template v-else>
        <el-table :data="auditLogs" size="small">
          <el-table-column label="时间" width="150">
            <template #default="{ row }">
              {{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}
            </template>
          </el-table-column>
          <el-table-column prop="actor_name" label="操作者" width="120" />
          <el-table-column prop="action_text" label="动作" width="110" />
          <el-table-column label="对象" width="150">
            <template #default="{ row }">
              {{ row.target_name || row.target_id }}
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="220">
            <template #default="{ row }">
              {{ detailText(row) }}
            </template>
          </el-table-column>
        </el-table>
        <div v-if="auditHasMore" class="more-wrap">
          <el-button size="small" :loading="auditLoading" @click="loadMoreAudit">加载更早的记录</el-button>
        </div>
      </template>
      <div class="hint">
        审计**永不清理** —— 它是"谁改了权限"的唯一凭据，价值随时间增长。
        （`agent_logs` 是运行数据、有过期策略；这张表刻意没有。）
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.sys {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.more-wrap {
  margin-top: 10px;
  text-align: center;
}
.head-tag {
  margin-left: 8px;
}
.deps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}
.dep {
  padding: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}
.dep.bad {
  border-color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.dep-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.dep-name {
  font-weight: 600;
}
.dep-note {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.vector-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.vector-item {
  padding: 10px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  text-align: center;
}
.vector-item.warn {
  border-color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}
.vnum {
  display: block;
  font-size: 20px;
  font-weight: 600;
}
.vector-item.warn .vnum {
  color: var(--el-color-warning);
}
.vlabel {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.down {
  display: flex;
  align-items: center;
  gap: 8px;
}
.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
</style>
