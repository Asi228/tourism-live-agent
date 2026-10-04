import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi } from '../api'
import { getToken, setToken } from '../api/http'

/**
 * 登录态。
 *
 * token 存在 localStorage（与后端调试台一致）：刷新页面不掉登录、实现简单；
 * 代价是有 XSS 风险面 —— 正式产品应改用 httpOnly Cookie。
 * 当前阶段（内部系统）够用，故只在这里集中管理，将来要换只改这一个文件。
 */
export const useUserStore = defineStore('user', () => {
  const token = ref(getToken())
  const profile = ref(null)

  const isLoggedIn = computed(() => !!token.value)
  // 后端 UserOut.role 是个对象（{id, code, name}），权限判断看 code
  const isAdmin = computed(() => profile.value?.role?.code === 'admin')
  const displayName = computed(() => profile.value?.username || '未登录')
  const roleName = computed(() => profile.value?.role?.name || '')

  async function login(username, password) {
    const body = await authApi.login({ username, password })
    token.value = body.data.access_token
    setToken(token.value)
    return fetchMe()
  }

  async function register(payload) {
    // 注册成功不自动登录（后端口径），前端注册完引导用户去登录
    return authApi.register(payload)
  }

  async function fetchMe() {
    const body = await authApi.me()
    profile.value = body.data
    return profile.value
  }

  function logout() {
    token.value = ''
    profile.value = null
    setToken('')
  }

  return { token, profile, isLoggedIn, isAdmin, displayName, roleName, login, register, fetchMe, logout }
})
