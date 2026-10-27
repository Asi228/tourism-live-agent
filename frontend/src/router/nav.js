import {
  Collection,
  ChatDotRound,
  Monitor,
  Odometer,
  Refresh,
  Warning,
  ShoppingCart,
  User,
} from '@element-plus/icons-vue'

/**
 * 侧边栏导航 —— **菜单文字与路由 meta.title 的唯一来源**。
 *
 * 以前这两处各写一份：路由里 6 条 `meta: { title: '概览' }`，MainLayout 的菜单里
 * 6 个 `<span>概览</span>`，字符串逐字相同。加一个页面要改两处，改一处就叫
 * "顶栏写着知识库、菜单高亮着文档管理" —— 而这种漂移**编译不报错、肉眼也容易漏**。
 *
 * 现在两边都从这张表派生：
 *   · `router/index.js` 用它生成 children 的 `meta.title` / `meta.admin`
 *   · `MainLayout.vue` 用它渲染菜单项（文字 + 图标 + 管理员过滤）
 * 新增页面只改这里一处。
 *
 * ⚠️ 顶栏**没有** `|| '概览'` 之类的兜底：那个兜底会在"新页面忘了配 title"时
 * 显示一个**谎**（用户以为自己在概览页）。现在标题为空就是空的，比说谎好。
 */
export const NAV_ITEMS = [
  { path: '/dashboard', title: '运营工作台', icon: Odometer },
  { path: '/commerce', title: '直播运营', icon: ShoppingCart },
  { path: '/automation', title: '自动化中心', icon: Refresh },
  { path: '/approvals', title: '审批中心', icon: Warning },
  { path: '/chat', title: '业务知识助手', icon: ChatDotRound },
  { path: '/knowledge', title: '知识管理', icon: Collection },
  // 管理入口由菜单层按角色隐藏，路由与后端仍然强校验。
  { path: '/users', title: '用户与权限', icon: User, admin: true },
  { path: '/system', title: '系统管理', icon: Monitor, admin: true },
]

// 二级能力保留路由，但不占用一级导航。文档从知识库进入，检索诊断仅供管理员排障。
export const SECONDARY_ROUTES = [
  { path: '/documents', title: '业务资料' },
  { path: '/search-debug', title: '检索诊断', admin: true },
]
