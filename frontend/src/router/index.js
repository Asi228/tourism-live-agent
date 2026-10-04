import { createRouter, createWebHashHistory } from 'vue-router'

import { getToken } from '../api/http'
import MainLayout from '../layouts/MainLayout.vue'

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
    redirect: '/knowledge',
    children: [
      { path: 'knowledge', name: 'knowledge', component: () => import('../views/Knowledge.vue') },
      { path: 'documents', name: 'documents', component: () => import('../views/Documents.vue') },
      { path: 'chat', name: 'chat', component: () => import('../views/Chat.vue') },
    ],
  },
  // 兜底：手输错地址时回首页，而不是白屏
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const logged = !!getToken()
  if (!to.meta.public && !logged) {
    // 记下原本想去哪，登录后可以跳回去（这里只做最简版：统一回首页）
    return { path: '/login' }
  }
  if (to.path === '/login' && logged) return { path: '/' }
  return true
})

export default router
