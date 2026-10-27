<script setup>
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { InfoFilled, Reading, SwitchButton } from '@element-plus/icons-vue'

import { useUserStore } from '../stores/user'
import { NAV_ITEMS } from '../router/nav'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

// 刷新页面后 store 是空的（内存态），但 token 还在 localStorage —— 这里补一次用户信息。
// 失败不拦：可能是 token 过期，http 拦截器已经处理跳登录。
onMounted(async () => {
  if (user.isLoggedIn && !user.profile) {
    try {
      await user.fetchMe()
    } catch {
      /* 已由拦截器提示 */
    }
  }
})

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定退出登录？', '提示', { type: 'warning' })
  } catch {
    return // 用户取消
  }
  user.logout()
  router.replace('/login')
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="brand">
        <div class="brand-logo"><el-icon><Reading /></el-icon></div>
        <div class="brand-text">
          <div class="brand-title">旅播智营</div>
          <div class="brand-sub">旅游直播运营平台</div>
        </div>
      </div>

      <!-- 菜单由 NAV_ITEMS 驱动（见 router/nav.js）：文字、图标、管理员过滤
           都从那一条来，路由的 meta.title 也是它生成的 —— 以前这里手写 6 个
           <span>、路由里再手写 6 个 meta.title，加页面要改两处，改漏就叫
           "顶栏与菜单对不上"，而这种漂移编译不报错。 -->
      <el-menu :default-active="route.path" router class="menu">
        <template v-for="item in NAV_ITEMS" :key="item.path">
          <!-- 管理入口只对管理员渲染。这是"权限差异感"：员工看不到的入口干脆不出现，
               而不是点进去才被后端拒。后端 require_admin 仍然强校验（这里只是体验层）。 -->
          <el-menu-item v-if="!item.admin || user.isAdmin" :index="item.path" :title="item.title">
            <el-icon><component :is="item.icon" /></el-icon>
            <span class="menu-label">{{ item.title }}</span>
          </el-menu-item>
        </template>
      </el-menu>

    </el-aside>

    <el-container>
      <el-header class="header">
        <!-- 显示**当前页标题**（来自路由 meta.title，由 router/nav.js 的 NAV_ITEMS 生成）。
             产品名在侧边栏品牌区 —— 两边都放同一句大字，用户会分不清"我现在在哪一页"。
             ⚠️ **刻意没有 `|| '概览'` 兜底**：那个兜底会在"新页面忘了配 title"时
             显示一个谎（用户以为自己还在概览页）。宁可空着，也不说谎。 -->
        <div class="header-title">{{ route.meta.title }}</div>
        <div class="header-right">
          <el-button text :icon="InfoFilled" @click="router.push('/intro')">项目介绍</el-button>
          <el-tag v-if="user.roleName" size="small" type="info">{{ user.roleName }}</el-tag>
          <span class="username">{{ user.displayName }}</span>
          <el-button link type="primary" @click="handleLogout">
            <el-icon><SwitchButton /></el-icon>
            退出
          </el-button>
        </div>
      </el-header>

      <el-main class="main">
        <!-- 子页面用 v-loading 各自控制自己的加载态，这里只做容器 -->
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
/* 高度取 100%（不是 100vh）：配合 common.css 里 `html,body,#app{height:100%}` 拿到确定高度。
   `overflow: hidden` 是**外壳不该产出滚动条**这条不变量的硬保证 ——
   应用外壳自己永远不滚，滚动只发生在内容区（.main / 各页自己的滚动容器）。
   （body 默认 8px 外边距已由全局基座归零；见 common.css 顶部的说明。） */
.layout {
  height: 100%;
  overflow: hidden;
}
.aside {
  display: flex;
  flex-direction: column;
  border-right: none;
  background: var(--app-sidebar);
  box-shadow: 4px 0 18px rgb(15 23 42 / 10%);
  transition: width .2s ease;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px 14px;
  color: white;
}
/* 品牌方块：给纯文字的品牌区一个视觉锚点，否则侧边栏顶部显得空 */
.brand-logo {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 8px;
  background: linear-gradient(145deg, #3b82f6, #06b6d4);
  color: var(--el-color-white);
  font-size: 20px;
}
.brand-title {
  font-size: 16px;
  font-weight: 600;
}
.brand-sub {
  font-size: var(--el-font-size-extra-small);
  color: #94a3b8;
  margin-top: 2px;
}
/* 菜单选中态：左侧色条 + 浅色底 + 加粗。
   色条用 ::before 而不是 border-left —— border 会把文字挤位，且 hover 时也带着边框 */
.menu :deep(.el-menu-item.is-active) {
  background: rgb(37 99 235 / 22%);
  color: #fff;
  font-weight: 600;
}
.menu :deep(.el-menu-item.is-active)::before {
  content: '';
  position: absolute;
  left: 0;
  top: 20%;
  bottom: 20%;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: #38bdf8;
}
/* 窗口变矮时菜单项放不下：以前是顶穿 100vh 让页面长出滚动条，
   现在 .layout 收了 overflow:hidden，那会变成**被裁掉且滚不出来** ——
   所以这里让它自己滚（min-height:0 同样是收缩前提）。 */
.menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  border-right: none;
  background: transparent;
}
.menu :deep(.el-menu-item) {
  height: 48px;
  margin: 3px 10px;
  padding: 0 14px !important;
  border-radius: 8px;
  color: #aebbd0;
}
.menu :deep(.el-menu-item:hover) {
  background: rgb(148 163 184 / 12%);
  color: #fff;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  border-bottom: 1px solid #e8ecf2;
  background: var(--el-bg-color);
}
/* 顶栏标题就是"我在哪一页"，字重字号要压得住 —— 否则用户得靠侧边栏高亮来判断位置 */
.header-title {
  font-size: 17px;
  font-weight: 650;
  color: var(--el-text-color-primary);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.username {
  font-size: 14px;
}
/* .el-main 的默认是 `flex:auto; overflow:auto`（实测），**没有 min-height:0** →
   flex 子项的自动最小尺寸等于内容最小高度，它可以**拒绝收缩**，
   把外壳顶穿、让根容器再长出一条滚动条。Chat 那两条滚动条里"外面那条"就是这么来的。
   归零之后：超出部分由它自己的 `overflow:auto` 消化，不会外溢到页面级。 */
.main {
  background: var(--app-page);
  padding: 20px;
  min-height: 0;
}
.main > :deep(*) {
  width: 100%;
  max-width: 1600px;
  margin-right: auto;
  margin-left: auto;
}

@media (max-width: 900px) {
  .aside { width: 72px !important; }
  .brand { justify-content: center; padding: 16px 10px; }
  .brand-text, .menu-label { display: none; }
  .menu :deep(.el-menu-item) { justify-content: center; margin: 4px 10px; padding: 0 !important; }
  .menu :deep(.el-menu-item .el-icon) { margin-right: 0; font-size: 19px; }
  .main { padding: 14px; }
}

@media (max-width: 620px) {
  .header { padding: 0 14px; }
  .header-right .el-tag { display: none; }
  .username { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
</style>
