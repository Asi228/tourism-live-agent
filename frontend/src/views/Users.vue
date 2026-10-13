<script setup>
/**
 * 用户管理页（仅系统管理员）。
 *
 * 四件事：看用户、**开通账号**、改角色、**停用 / 启用**，以及看某人被授权了哪些库。
 *
 * 关于"改角色"这条**提权入口**，前端这边要配合三件事（后端已强制，这里只是把话说清）：
 *  1. **自己那一行的下拉禁用** —— 后端会拒（"不能修改自己的角色"），但让用户点了
 *     再吃一个错误提示是糟糕的交互；直接灰掉，并在 tooltip 里说明原因。
 *  2. **最后一个管理员特殊标注** —— 后端也会拒，同理提前提示。
 *  3. **改完把后端返回的 `changed` 区分出来** —— 幂等提交（改成同一个角色）时它是
 *     false，提示"未发生变化"，而不是把幂等当成功当失败。
 *
 * 「停用」同理：自己那一行禁用（防自锁），其余交给后端两道闸门。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'

import { userApi } from '../api'
import { useUserStore } from '../stores/user'

const me = useUserStore()
const router = useRouter()

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

// ==================== 开通账号 ====================
//
// 自助注册默认是关的（`AUTH_ALLOW_REGISTER`），所以这里才是主要的开号入口。
const createVisible = ref(false)
const creating = ref(false)
const createFormRef = ref()
const createForm = reactive({
  username: '',
  email: '',
  password: '',
  role_code: 'employee', // 默认最小权限
})

// 与后端 `UserCreateRequest`（继承注册的 `RegisterRequest`）的规则保持一致 ——
// 不一致的话会出现"前端放过去、后端打回来"，用户看到的是一条看不懂的报错
const createRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '3-64 位', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_]+$/, message: '只能是字母、数字、下划线', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, max: 72, message: '6-72 位', trigger: 'blur' },
  ],
}

function openCreate() {
  Object.assign(createForm, { username: '', email: '', password: '', role_code: 'employee' })
  createVisible.value = true
}

async function doCreate() {
  const ok = await createFormRef.value.validate().catch(() => false)
  if (!ok) return
  creating.value = true
  try {
    const body = await userApi.create({ ...createForm })
    ElMessage.success(body.message || '已开通账号')
    createVisible.value = false
    await load()
  } catch {
    /* 重名 / 角色不存在 / 权限不足都由拦截器提示，弹窗保持打开让人改 */
  } finally {
    creating.value = false
  }
}

// ==================== 停用 / 启用 ====================

function statusDisabled(row) {
  // 自己那一行不给点：后端会拒（"不能停用自己的账号"），这里先灰掉免得白点
  return row.id === me.profile?.id
}

async function toggleStatus(row) {
  const next = !row.is_active
  const verb = next ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(
      next
        ? `确定启用「${row.username}」？启用后他可以重新登录。`
        : `确定停用「${row.username}」？停用后他立刻无法登录，`
          + '手上已经签发的登录状态（token）也会在下一次请求就失效。',
      `${verb}账号`,
      { type: 'warning', confirmButtonText: `确定${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    const body = await userApi.setStatus(row.id, next)
    if (body.data?.changed === false) {
      ElMessage.info('状态未发生变化')
    } else {
      ElMessage.success(body.message || `已${verb}`)
    }
    await load()
  } catch {
    await load() // 失败（含后端拒绝）：以服务端状态为准
  }
}

// ==================== 重置密码 ====================
//
// 与"改角色 / 停用"不同，**允许重置自己** —— 改密码不构成自锁（你知道自己设的新密码），
// 禁掉反而会让"管理员忘了密码"变成只能改数据库。
// 代价是：重置成功后**该用户已签发的 token 全部失效**（token 里带密码指纹），
// 所以重置自己时要把当前会话也一起结束掉。
const pwdVisible = ref(false)
const pwdUser = ref(null)
const pwdSaving = ref(false)
const pwdFormRef = ref()
const pwdForm = reactive({ new_password: '' })

const pwdRules = {
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, max: 72, message: '6-72 位', trigger: 'blur' },
  ],
}

function openResetPwd(row) {
  pwdUser.value = row
  pwdForm.new_password = ''
  pwdVisible.value = true
}

async function doResetPwd() {
  const ok = await pwdFormRef.value.validate().catch(() => false)
  if (!ok) return
  const isSelf = pwdUser.value?.id === me.profile?.id
  try {
    await ElMessageBox.confirm(
      isSelf
        ? '这是你自己的账号。重置后你的当前登录状态会立刻结束，需要用新密码重新登录。确定继续？'
        : `确定重置「${pwdUser.value?.username}」的密码？`
          + '重置后他的旧密码失效，已登录的设备也会被登出。',
      '重置密码',
      { type: 'warning', confirmButtonText: '确定重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  pwdSaving.value = true
  try {
    const body = await userApi.resetPassword(pwdUser.value.id, pwdForm.new_password)
    pwdVisible.value = false
    ElMessage.success(body.message || '已重置密码')
    if (body.data?.is_self) {
      // 当前这张票已经作废了，主动收场，别等下一个请求莫名 401
      me.logout()
      router.replace('/login')
      return
    }
    await load()
  } catch {
    /* 长度不合规 / 权限不足由拦截器提示，弹窗保持打开 */
  } finally {
    pwdSaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <el-card>
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
            <el-button type="primary" @click="openCreate">新建用户</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" class="tip">
        角色的含义：<b>管理员</b>能建库、管成员、改可见性、看系统状态与审计；
        <b>普通员工</b>只能访问被授权的知识库。
        <br>
        账号由管理员在这里开通（<b>自助注册默认关闭</b>）。
        ⚠️ 刻意<b>不提供"删除用户"</b> —— 删用户会牵连他的会话与审计记录，不可逆；
        要收回访问权请用<b>停用</b>：它会立刻拒绝登录，已签发的登录态下一次请求即失效，
        而且随时可以再启用。
        <br>
        用户忘了密码？用<b>重置密码</b> —— 旧密码与该用户所有登录态会一起失效。
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
        <el-table-column label="角色" width="150">
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
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '启用' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="150">
          <template #default="{ row }">
            {{ (row.created_at || '').replace('T', ' ').slice(0, 16) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230">
          <template #default="{ row }">
            <el-button link type="primary" @click="openGrants(row)">授权情况</el-button>
            <el-button link type="warning" @click="openResetPwd(row)">重置密码</el-button>
            <el-tooltip
              :disabled="!statusDisabled(row)"
              content="不能停用自己的账号（会被锁在系统外）"
            >
              <el-button
                link
                :type="row.is_active ? 'danger' : 'success'"
                :disabled="statusDisabled(row)"
                @click="toggleStatus(row)"
              >
                {{ row.is_active ? '停用' : '启用' }}
              </el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createVisible" title="开通账号" width="460px">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="80px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" placeholder="字母 / 数字 / 下划线，3-64 位" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="createForm.email" placeholder="用于登录与身份识别" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            placeholder="6-72 位，请线下告知本人"
          />
        </el-form-item>
        <el-form-item label="角色" prop="role_code">
          <el-select v-model="createForm.role_code" style="width: 100%">
            <el-option v-for="r in roles" :key="r.code" :label="r.name" :value="r.code" />
          </el-select>
        </el-form-item>
        <el-alert type="warning" :closable="false">
          开通后会写一条审计（谁、什么时候、给谁开的、什么角色）。
          初始密码请通过<b>线下渠道</b>告知本人 —— 这条链路里没有"首次登录强制改密"。
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">开通</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pwdVisible" :title="`重置 ${pwdUser?.username} 的密码`" width="430px">
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="80px">
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="pwdForm.new_password"
            type="password"
            show-password
            placeholder="6-72 位"
            @keyup.enter="doResetPwd"
          />
        </el-form-item>
        <el-alert type="warning" :closable="false">
          重置后：旧密码立刻失效，该用户<b>已登录的设备也会被登出</b>
          （登录凭证里带着旧密码的指纹）。请通过<b>线下渠道</b>把新密码告知本人。
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="doResetPwd">重置</el-button>
      </template>
    </el-dialog>

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
.tip {
  margin-bottom: 12px;
}
.hint {
  margin-top: 10px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
}
</style>
