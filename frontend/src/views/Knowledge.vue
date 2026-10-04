<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { knowledgeApi, userApi } from '../api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const user = useUserStore()

const loading = ref(false)
const rows = ref([])

const editVisible = ref(false)
const editing = ref(null) // null = 新建
const form = reactive({ name: '', description: '', is_public: false })
const formRef = ref()
const formRules = {
  name: [
    { required: true, message: '请输入名称', trigger: 'blur' },
    { max: 64, message: '不超过 64 个字符', trigger: 'blur' },
  ],
}

const memberVisible = ref(false)
const memberKb = ref(null)
const members = ref([])
const grantForm = reactive({ user_id: null, permission: 'read' })
const candidates = ref([])

// 库级权限：admin 可以改库和管成员，write 可以传文档，read 只能看
function canManage(kb) {
  return user.isAdmin || kb.my_permission === 'admin'
}

const permissionText = { read: '只读', write: '可写', admin: '管理' }
const permissionTagType = { read: 'info', write: 'warning', admin: 'success' }

async function load() {
  loading.value = true
  try {
    const body = await knowledgeApi.list()
    rows.value = body.data || []
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.name = ''
  form.description = ''
  form.is_public = false
  editVisible.value = true
}

function openEdit(kb) {
  editing.value = kb
  form.name = kb.name
  form.description = kb.description || ''
  form.is_public = !!kb.is_public
  editVisible.value = true
}

async function submitForm() {
  const ok = await formRef.value.validate().catch(() => false)
  if (!ok) return
  const payload = {
    name: form.name.trim(),
    description: form.description?.trim() || null,
    is_public: form.is_public,
  }
  try {
    if (editing.value) {
      await knowledgeApi.update(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await knowledgeApi.create(payload)
      ElMessage.success('已创建')
    }
    editVisible.value = false
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

async function removeKb(kb) {
  try {
    await ElMessageBox.confirm(
      `删除知识库「${kb.name}」会一并删掉它的文档、切分块、向量和磁盘文件，且不可恢复。确定继续？`,
      '危险操作',
      { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
    )
  } catch {
    return
  }
  try {
    await knowledgeApi.remove(kb.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

async function openMembers(kb) {
  memberKb.value = kb
  memberVisible.value = true
  grantForm.user_id = null
  grantForm.permission = 'read'
  await Promise.all([loadMembers(), loadCandidates()])
}

async function loadMembers() {
  try {
    const body = await knowledgeApi.members(memberKb.value.id)
    members.value = body.data || []
  } catch {
    members.value = []
  }
}

async function loadCandidates() {
  // 只有系统管理员能列用户；非管理员打开时这个下拉就是空的，不影响看成员列表
  if (!user.isAdmin) {
    candidates.value = []
    return
  }
  try {
    const body = await userApi.list()
    const exists = new Set(members.value.map((m) => m.user_id))
    candidates.value = (body.data || []).filter(
      (u) => !exists.has(u.id) && u.id !== memberKb.value.owner_id
    )
  } catch {
    candidates.value = []
  }
}

async function grant() {
  if (!grantForm.user_id) {
    ElMessage.warning('请先选择用户')
    return
  }
  try {
    await knowledgeApi.grant(memberKb.value.id, {
      user_id: grantForm.user_id,
      permission: grantForm.permission,
    })
    ElMessage.success('已授权')
    grantForm.user_id = null
    await Promise.all([loadMembers(), loadCandidates()])
  } catch {
    /* 拦截器已提示 */
  }
}

async function revoke(row) {
  try {
    await ElMessageBox.confirm(`移除成员 ${row.username}？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await knowledgeApi.revoke(memberKb.value.id, row.user_id)
    ElMessage.success('已移除')
    await Promise.all([loadMembers(), loadCandidates()])
  } catch {
    /* 拦截器已提示 */
  }
}

function goDocuments(kb) {
  router.push({ path: '/documents', query: { kb: kb.id } })
}

const isEmpty = computed(() => !loading.value && rows.value.length === 0)

onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>知识库</span>
          <div class="head-actions">
            <el-button :loading="loading" @click="load">刷新</el-button>
            <el-button v-if="user.isAdmin" type="primary" @click="openCreate">新建知识库</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="rows" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="可见性" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_public ? 'success' : 'info'">
              {{ row.is_public ? '公开' : '私有' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="我的权限" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="permissionTagType[row.my_permission] || 'info'">
              {{ permissionText[row.my_permission] || row.my_permission }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建者" width="120">
          <template #default="{ row }">{{ row.owner?.username || row.owner_id }}</template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="goDocuments(row)">文档</el-button>
            <!-- 「成员」只给系统管理员：抽屉里的授权表单要靠 /api/users 列用户，而那个接口
                 是 require_admin。给库级 admin 开这个入口只会让他"看得到列表、加不了人" ——
                 与其做个半开的能力，不如先把口径收齐（后端放宽接口后再一起放开）。 -->
            <el-button v-if="user.isAdmin" link type="primary" @click="openMembers(row)">成员</el-button>
            <el-button v-if="canManage(row)" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="user.isAdmin" link type="danger" @click="removeKb(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="isEmpty" description="还没有知识库">
        <el-button v-if="user.isAdmin" type="primary" @click="openCreate">新建一个</el-button>
        <span v-else class="hint">请联系管理员为你授权</span>
      </el-empty>
    </el-card>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="editVisible" :title="editing ? '编辑知识库' : '新建知识库'" width="480px">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="例如：员工手册" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="可选" />
        </el-form-item>
        <el-form-item label="公开">
          <el-switch v-model="form.is_public" />
          <span class="form-hint">公开后所有登录用户都能检索到它</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm">确定</el-button>
      </template>
    </el-dialog>

    <!-- 成员管理 -->
    <el-drawer v-model="memberVisible" :title="`成员管理 · ${memberKb?.name || ''}`" size="520px">
      <div v-if="memberKb" class="owner-line">
        创建者：<b>{{ memberKb.owner?.username || memberKb.owner_id }}</b>（默认拥有全部权限）
      </div>

      <el-table :data="members" size="small" stripe>
        <el-table-column prop="username" label="用户" min-width="120" />
        <el-table-column label="权限" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="permissionTagType[row.permission] || 'info'">
              {{ permissionText[row.permission] || row.permission }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="danger" @click="revoke(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-divider>添加成员</el-divider>

      <!-- 这个抽屉只有系统管理员能打开（「成员」按钮同样收口），所以这里不再做二次判断；
           选人下拉依赖 /api/users，而它是 require_admin。 -->
      <div class="grant-row">
        <el-select v-model="grantForm.user_id" placeholder="选择用户" filterable style="flex: 1">
          <el-option v-for="u in candidates" :key="u.id" :label="`${u.username}（id=${u.id}）`" :value="u.id" />
        </el-select>
        <el-select v-model="grantForm.permission" style="width: 110px">
          <el-option label="只读" value="read" />
          <el-option label="可写" value="write" />
          <el-option label="管理" value="admin" />
        </el-select>
        <el-button type="primary" @click="grant">授权</el-button>
      </div>
    </el-drawer>
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
.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.form-hint {
  margin-left: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.owner-line {
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.grant-row {
  display: flex;
  gap: 8px;
}
</style>
