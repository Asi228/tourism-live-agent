import { createRouter, createWebHashHistory } from 'vue-router'

import { getToken } from '../api/http'
import MainLayout from '../layouts/MainLayout.vue'
import { useUserStore } from '../stores/user'

/**
 * 用 **hash 模式**（URL 形如 /#/chat）而不是 history：
 * 前者不需要任何服务器 rewrite 规则，扔到任意静态服务器/nginx 子路径下都能跑。
 * 内部系统不追求 URL 好看，少一处部署坑更值。
 */
const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    // 默认落在概览页而不是知识库：第一屏应该是"我现在有什么、发生了什么"，
    // 而不是一个功能列表。`meta.admin` 的页面由菜单层隐藏，路由层只做登录校验。
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
      { path: 'knowledge', name: 'knowledge', component: () => import('../views/Knowledge.vue') },
      { path: 'documents', name: 'documents', component: () => import('../views/Documents.vue') },
      { path: 'chat', name: 'chat', component: () => import('../views/Chat.vue') },
      { path: 'users', name: 'users', component: () => import('../views/Users.vue'), meta: { admin: true } },
      { path: 'system', name: 'system', component: () => import('../views/System.vue'), meta: { admin: true } },
    ],
  },
  // 兜底：手输错地址时回首页，而不是白屏
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const logged = !!getToken()
  if (!to.meta.public && !logged) {
    // 记下原本想去哪，登录后可以跳回去（这里只做最简版：统一回首页）
    return { path: '/login' }
  }
  if (to.path === '/login' && logged) return { path: '/' }

  // 管理页的守卫。**菜单隐藏只是体验层，这里才是路由层的第一道**（后端
  // require_admin 是最后一道、也是最硬的一道）。
  // ⚠️ 必须 `ensureProfile()`：profile 是异步拉的，刷新页面时它还是 null、
  // isAdmin 恒为 false —— 直接判会把管理员也踢走。ensureProfile 内部用单例
  // promise，多个守卫并发调用只发一次请求。
  if (to.meta.admin) {
    const user = useUserStore()
    try {
      await user.ensureProfile()
    } catch {
      // 取不到身份（token 过期等）：交给 http 拦截器处理跳登录，这里先放行，
      // 页面的接口请求会以 40100/40300 再次拦住 —— 数据安全不依赖这一跳。
      return true
    }
    if (!user.isAdmin) return { path: '/dashboard' }
  }
  return true
})

export default router
