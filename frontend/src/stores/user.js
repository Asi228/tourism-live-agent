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

  /**
   * 保证 `profile` 已加载（并发安全）。
   *
   * 为什么需要它：`profile` 是**异步**拉的，而路由守卫要在进入页面前就知道
   * "这个人是不是管理员" —— 刷新页面时 `profile` 是 null、`isAdmin` 是 false，
   * 守卫会把管理员误判成员工踢走。
   *
   * 用模块级 promise 做单例：多个守卫 / 组件同时调用时只会发**一次**请求，
   * 而不是每人发一次。请求结束（无论成败）后清掉，失败可以重试。
   */
  let profilePromise = null

  async function ensureProfile() {
    if (profile.value) return profile.value
    if (!profilePromise) {
      profilePromise = fetchMe().finally(() => {
        profilePromise = null
      })
    }
    return profilePromise
  }

  function logout() {
    token.value = ''
    profile.value = null
    profilePromise = null
    setToken('')
  }

  return {
    token, profile, isLoggedIn, isAdmin, displayName, roleName,
    login, register, fetchMe, ensureProfile, logout,
  }
})
