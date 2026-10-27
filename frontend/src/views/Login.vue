<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { authApi } from '../api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const user = useUserStore()

const tab = ref('login')
const loading = ref(false)
const loginFormRef = ref()
const regFormRef = ref()

// 自助注册默认**关**（企业里账号由管理员开通），所以这里初值也是 false：
// 先当成"不显示"，拿到后端的 true 才渲染出注册页签。
// ⚠️ 反过来（先显示、拿到 false 再隐藏）会让默认部署下**闪一下**
// 一个点进去必然报错的入口 —— 保守方向的初值在这种开关上是免费的。
const allowRegister = ref(false)

onMounted(async () => {
  try {
    const body = await authApi.config()
    allowRegister.value = !!body.data?.allow_register
  } catch {
    // 拿不到配置就当关着（与默认值一致）—— 这是登录页，不能因为这个请求失败而卡住
  }
})

const loginForm = reactive({ username: '', password: '' })
const regForm = reactive({ username: '', email: '', password: '', confirm: '' })

const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

function fillDemoAccount() {
  tab.value = 'login'
  loginForm.username = 'demo_operator'
  loginForm.password = 'Demo@123456'
}

// 与后端 RegisterRequest 的校验保持一致，避免"点了提交才被后端打回"
const regRules = {
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
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 72, message: '6-72 位', trigger: 'blur' },
  ],
  confirm: [
    {
      validator: (_rule, value, cb) => {
        if (!value) return cb(new Error('请再次输入密码'))
        if (value !== regForm.password) return cb(new Error('两次输入的密码不一致'))
        cb()
      },
      trigger: 'blur',
    },
  ],
}

async function doLogin() {
  const ok = await loginFormRef.value.validate().catch(() => false)
  if (!ok) return
  loading.value = true
  try {
    await user.login(loginForm.username, loginForm.password)
    ElMessage.success('登录成功')
    router.replace('/')
  } catch {
    /* 错误提示已由 http 拦截器统一给出 */
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  const ok = await regFormRef.value.validate().catch(() => false)
  if (!ok) return
  loading.value = true
  try {
    await user.register({
      username: regForm.username,
      email: regForm.email,
      password: regForm.password,
    })
    // 后端口径：注册成功不自动登录，引导用户去登录
    ElMessage.success('注册成功，请使用新账号登录')
    loginForm.username = regForm.username
    loginForm.password = ''
    tab.value = 'login'
  } catch {
    /* 同上 */
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="card">
      <div class="head">
        <div class="brand-mark">旅</div>
        <div class="title">旅播智营</div>
        <div class="sub">旅游直播电商智能运营管理平台</div>
        <div class="value-line">商品管理 · 开播检查 · 业务问答 · 审批执行</div>
      </div>

      <el-tabs v-model="tab" stretch>
        <el-tab-pane label="登录" name="login">
          <button class="demo-account" type="button" @click="fillDemoAccount">
            <span><strong>访客演示账号</strong><small>已准备旅游商品、直播场次和知识库数据</small></span>
            <b>一键填入</b>
          </button>
          <el-form ref="loginFormRef" :model="loginForm" :rules="loginRules" @submit.prevent="doLogin">
            <el-form-item prop="username">
              <el-input v-model="loginForm.username" placeholder="用户名" size="large" clearable />
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                v-model="loginForm.password"
                type="password"
                placeholder="密码"
                size="large"
                show-password
                @keyup.enter="doLogin"
              />
            </el-form-item>
            <el-button type="primary" size="large" class="submit" :loading="loading" @click="doLogin">
              登录
            </el-button>
          </el-form>
        </el-tab-pane>

        <!-- 自助注册默认关（`AUTH_ALLOW_REGISTER`）。关着的时候整个页签不渲染 ——
             与其让人点进去再被后端 403 打回，不如这个入口根本不出现。 -->
        <el-tab-pane v-if="allowRegister" label="注册" name="register">
          <el-form ref="regFormRef" :model="regForm" :rules="regRules" @submit.prevent="doRegister">
            <el-form-item prop="username">
              <el-input v-model="regForm.username" placeholder="用户名（字母/数字/下划线）" size="large" clearable />
            </el-form-item>
            <el-form-item prop="email">
              <el-input v-model="regForm.email" placeholder="邮箱" size="large" clearable />
            </el-form-item>
            <el-form-item prop="password">
              <el-input v-model="regForm.password" type="password" placeholder="密码（至少 6 位）" size="large" show-password />
            </el-form-item>
            <el-form-item prop="confirm">
              <el-input v-model="regForm.confirm" type="password" placeholder="确认密码" size="large" show-password @keyup.enter="doRegister" />
            </el-form-item>
            <el-button type="primary" size="large" class="submit" :loading="loading" @click="doRegister">
              注册
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
      <el-button text class="intro-link" @click="router.push('/intro')">← 返回项目介绍</el-button>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #eef2ff 0%, #f8fafc 100%);
}
.card {
  width: 380px;
  padding: 28px 28px 20px;
  background: var(--el-bg-color);
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08);
}
.head {
  text-align: center;
  margin-bottom: 14px;
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  margin: 0 auto 12px;
  border-radius: 15px;
  color: #fff;
  background: linear-gradient(145deg, #2563eb, #0891b2);
  box-shadow: 0 9px 24px rgb(37 99 235 / 22%);
  font-size: 21px;
  font-weight: 700;
}
.title {
  font-size: 22px;
  font-weight: 700;
}
.sub {
  margin-top: 6px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.value-line {
  margin-top: 10px;
  color: #64748b;
  font-size: 11px;
  letter-spacing: .04em;
}
.submit {
  width: 100%;
}
.demo-account {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  margin-bottom: 16px;
  padding: 11px 13px;
  border: 1px solid #bfdbfe;
  border-radius: 10px;
  color: #1e3a8a;
  background: #eff6ff;
  text-align: left;
  cursor: pointer;
}
.demo-account span { display: grid; gap: 3px; }
.demo-account strong { font-size: 13px; }
.demo-account small { color: #64748b; font-size: 11px; }
.demo-account b { flex: 0 0 auto; margin-left: 10px; color: #2563eb; font-size: 12px; }
.demo-account:hover { border-color: #60a5fa; background: #dbeafe; }
.intro-link {
  display: flex;
  margin: 8px auto 0;
  color: #64748b;
}
</style>
