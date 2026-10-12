<script setup>
/**
 * 用户管理页（仅系统管理员）。
 *
 * 三件事：看用户、改角色、看某人被授权了哪些库。
 *
 * 关于"改角色"这条**提权入口**，前端这边要配合三件事（后端已强制，这里只是把话说清）：
 *  1. **自己那一行的下拉禁用** —— 后端会拒（"不能修改自己的角色"），但让用户点了
 *     再吃一个错误提示是糟糕的交互；直接灰掉，并在 tooltip 里说明原因。
 *  2. **最后一个管理员特殊标注** —— 后端也会拒，同理提前提示。
 *  3. **改完把后端返回的 `changed` 区分出来** —— 幂等提交（改成同一个角色）时它是
 *     false，提示"未发生变化"，而不是把幂等当成功当失败。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { userApi } from '../api'
import { useUserStore } from '../stores/user'

const me = useUserStore()

const loading = ref(false)
const rows = ref([])
const keyword = ref('')
const roles = ref([
  { code: 'admin', name: '管理员' },
  { code: 'employee', name: '普通员工' },
])

// 系统里有几个管理员 —— 用来提前提示"这是最后一个"，避免点了才被后端拒
const adminCount = computed(() => rows.value.filter((u) => u.role?.code === 'admin').length)

const grantVisible = ref(false)
const grantUser = ref(null)
const grants = ref([])
const grantLoading = ref(false)

async function load() {
  loading.value = true
  try {
    // full=true：管理页要看邮箱与注册时间（默认的精简结构是给"选人授权"用的）
    const body = await userApi.list(keyword.value.trim() || undefined, true)
    rows.value = body.data || []
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function roleDisabled(row) {
  if (row.id === me.profile?.id) return true
  // 最后一个管理员：下拉禁用（后端也会拒，这里只是不让人白点一下）
  return row.role?.code === 'admin' && adminCount.value <= 1
}

function roleDisabledReason(row) {
  if (row.id === me.profile?.id) return '不能修改自己的角色（防止把自己锁在系统外）'
  if (row.role?.code === 'admin' && adminCount.value <= 1) {
    return '系统里至少要保留一个管理员（这位是最后一个）'
  }
  return ''
}

async function changeRole(row, nextCode) {
  const prev = row.role?.code
  if (nextCode === prev) return
  const targetName = row.username
  try {
    await ElMessageBox.confirm(
      `确定把「${targetName}」的角色从 ${prev} 改成 ${nextCode}？这会改变他能看到的内容。`,
      '变更角色',
      { type: 'warning', confirmButtonText: '确定变更', cancelButtonText: '取消' },
    )
  } catch {
    await load() // 取消：把下拉回滚到原值
    return
  }
  try {
    const body = await userApi.updateRole(row.id, nextCode)
    if (body.data?.changed === false) {
      ElMessage.info('角色未发生变化')
    } else {
      ElMessage.success(body.message || '已变更')
    }
    await load()
  } catch {
    await load() // 失败（含后端拒绝）：以服务端状态为准
  }
}

async function openGrants(row) {
  grantUser.value = row
  grantVisible.value = true
  grantLoading.value = true
  grants.value = []
  try {
    const body = await userApi.grants(row.id)
    grants.value = body.data || []
  } catch {
    /* 拦截器已提示 */
  } finally {
    grantLoading.value = false
  }
}

const PERM_TEXT = { read: '只读', write: '可写', admin: '管理' }
const PERM_TYPE = { read: 'info', write: 'warning', admin: 'success' }

onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>用户管理</span>
          <div class="head-actions">
            <el-input
              v-model="keyword"
              placeholder="按用户名 / 邮箱搜索"
              clearable
              style="width: 200px"
              @keyup.enter="load"
              @clear="load"
            />
            <el-button :loading="loading" @click="load">查询</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" class="tip">
        角色的含义：<b>管理员</b>能建库、管成员、改可见性、看系统状态与审计；
        <b>普通员工</b>只能访问被授权的知识库。
        <br>
        ⚠️ 本页只做两件事：看人、改角色。刻意**不提供"删除用户"** ——
        删用户会牵连他的会话与审计记录，不可逆。
        账号**停用**需要 `is_active` 的接口，当前版本未开放：这是已知缺口，不是漏写。
      </el-alert>

      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="140">
          <template #default="{ row }">
            {{ row.username }}
            <el-tag v-if="row.id === me.profile?.id" size="small" type="info">我</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
        <el-table-column label="角色" width="170">
          <template #default="{ row }">
            <el-tooltip :disabled="!roleDisabled(row)" :content="roleDisabledReason(row)">
              <el-select
                :model-value="row.role?.code"
                size="small"
                :disabled="roleDisabled(row)"
                @change="(v) => changeRole(row, v)"
              >
                <el-option v-for="r in roles" :key="r.code" :label="r.name" :value="r.code" />
              </el-select>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="170">
          <template #default="{ row }">
            {{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button link type="primary" @click="openGrants(row)">授权情况</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="grantVisible" :title="`${grantUser?.username} 的知识库权限`" width="520px">
      <div v-loading="grantLoading">
        <el-empty v-if="!grants.length" description="没有被授权任何知识库" :image-size="70" />
        <el-table v-else :data="grants" size="small">
          <el-table-column prop="knowledge_base_id" label="库 ID" width="80" />
          <el-table-column prop="knowledge_base_name" label="知识库" show-overflow-tooltip />
          <el-table-column label="权限" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="PERM_TYPE[row.permission] || 'info'">
                {{ PERM_TEXT[row.permission] || row.permission }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div class="hint">
          管理员不受此表限制 —— 他默认能看到所有知识库。
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.tip {
  margin-bottom: 12px;
}
.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
