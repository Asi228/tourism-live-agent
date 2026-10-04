<script setup>
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { useUserStore } from '../stores/user'

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
        <div class="brand-title">企业知识库</div>
        <div class="brand-sub">智能 Agent</div>
      </div>

      <el-menu :default-active="route.path" router class="menu">
        <el-menu-item index="/knowledge">
          <span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/documents">
          <span>文档管理</span>
        </el-menu-item>
        <el-menu-item index="/chat">
          <span>智能问答</span>
        </el-menu-item>
      </el-menu>

      <div class="aside-foot">
        <a href="/devtools/" target="_blank" rel="noopener">打开后端调试台 →</a>
      </div>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">企业内部文档问答</div>
        <div class="header-right">
          <el-tag v-if="user.roleName" size="small" type="info">{{ user.roleName }}</el-tag>
          <span class="username">{{ user.displayName }}</span>
          <el-button link type="primary" @click="handleLogout">退出</el-button>
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
.layout {
  height: 100vh;
}
.aside {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-light);
  background: #fff;
}
.brand {
  padding: 18px 16px 12px;
}
.brand-title {
  font-size: 16px;
  font-weight: 600;
}
.brand-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
.menu {
  flex: 1;
  border-right: none;
}
.aside-foot {
  padding: 12px 16px;
  font-size: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.aside-foot a {
  color: var(--el-color-primary);
  text-decoration: none;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-light);
  background: #fff;
}
.header-title {
  font-size: 14px;
  color: var(--el-text-color-regular);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.username {
  font-size: 14px;
}
.main {
  background: var(--el-fill-color-light);
  padding: 16px;
}
</style>
