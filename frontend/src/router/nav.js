import {
  ChatDotRound,
  Collection,
  Document,
  Monitor,
  Odometer,
  Search,
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
  { path: '/dashboard', title: '概览', icon: Odometer },
  { path: '/knowledge', title: '知识库', icon: Collection },
  { path: '/documents', title: '文档管理', icon: Document },
  { path: '/chat', title: '智能问答', icon: ChatDotRound },
  // admin: true 的两项由菜单层按角色隐藏（体验层），路由层与后端仍然强校验
  { path: '/users', title: '用户管理', icon: User, admin: true },
  { path: '/system', title: '系统状态', icon: Monitor, admin: true },
  // 检索调试：只检索不调模型，看双路召回/融合/重排的原始结果
  // （原 devtools 单文件页的高频用途；devtools 仍保留作万能请求调试台）
  { path: '/search-debug', title: '检索调试', icon: Search, admin: true },
]
