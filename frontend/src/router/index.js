import { createRouter, createWebHashHistory } from 'vue-router'

import { getToken } from '../api/http'
import MainLayout from '../layouts/MainLayout.vue'
import { useUserStore } from '../stores/user'
import { NAV_ITEMS, SECONDARY_ROUTES } from './nav'

/**
 * 用 **hash 模式**（URL 形如 /#/chat）而不是 history：
 * 前者不需要任何服务器 rewrite 规则，扔到任意静态服务器/nginx 子路径下都能跑。
 * 内部系统不追求 URL 好看，少一处部署坑更值。
 */

// 页面组件按 name 懒加载。与 NAV_ITEMS 分开写（而不是在 nav.js 里直接放组件）：
// nav.js 是**纯配置**，不该知道 Vue 组件的存在；而路由这层本来就要显式列出
// "哪个路径对应哪个视图"，用查表比 `import.meta.glob` 更利于静态分析。
const VIEW_LOADERS = {
  dashboard: () => import('../views/Dashboard.vue'),
  commerce: () => import('../views/Commerce.vue'),
  automation: () => import('../views/Automation.vue'),
  approvals: () => import('../views/Approvals.vue'),
  knowledge: () => import('../views/Knowledge.vue'),
  documents: () => import('../views/Documents.vue'),
  chat: () => import('../views/Chat.vue'),
  users: () => import('../views/Users.vue'),
  system: () => import('../views/System.vue'),
  'search-debug': () => import('../views/SearchDebug.vue'),
}

// children 由 NAV_ITEMS 生成：meta.title / meta.admin 都从那张表来，
// 不再在这里手写第二份（否则顶栏与菜单各一套文字，迟早漂移）。
const layoutChildren = [...NAV_ITEMS, ...SECONDARY_ROUTES].map(({ path, title, icon: _icon, admin }) => {
  const name = path.replace(/^\//, '')
  const loader = VIEW_LOADERS[name]
  if (!loader) {
    throw new Error(`NAV_ITEMS 里的 ${path} 没有对应的视图组件，请补 VIEW_LOADERS`)
  }
  return { path: name, name, component: loader, meta: { title, admin } }
})

const routes = [
  { path: '/intro', name: 'intro', component: () => import('../views/Intro.vue'), meta: { public: true } },
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    // 默认落在概览页而不是知识库：第一屏应该是"我现在有什么、发生了什么"，
    // 而不是一个功能列表。`meta.admin` 的页面由**菜单层隐藏**（体验层），
    // 路由层下面的 beforeEach 仍有 admin 守卫（手输 URL 也会被拦走），
    // 后端 require_admin 是最后一道。
    redirect: () => getToken() ? '/dashboard' : '/intro',
    children: layoutChildren,
  },
  // 兜底：手输错地址时回首页，而不是白屏
  { path: '/:pathMatch(.*)*', redirect: () => getToken() ? '/dashboard' : '/intro' },
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
