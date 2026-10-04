<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useUserStore } from '../stores/user'

const router = useRouter()
const user = useUserStore()

const tab = ref('login')
const loading = ref(false)
const loginFormRef = ref()
const regFormRef = ref()

const loginForm = reactive({ username: '', password: '' })
const regForm = reactive({ username: '', email: '', password: '', confirm: '' })

const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
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
        <div class="title">企业知识库智能 Agent</div>
        <div class="sub">答案必须来自公司文档，并带可核对的引用来源</div>
      </div>

      <el-tabs v-model="tab" stretch>
        <el-tab-pane label="登录" name="login">
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

        <el-tab-pane label="注册" name="register">
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
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08);
}
.head {
  text-align: center;
  margin-bottom: 8px;
}
.title {
  font-size: 18px;
  font-weight: 600;
}
.sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.submit {
  width: 100%;
}
</style>
