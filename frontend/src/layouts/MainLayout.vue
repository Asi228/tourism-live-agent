<script setup>
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { useUserStore } from '../stores/user'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

// 应急调试台的地址：dev 下前端在 5173、后端在 8000，**不同源**，必须显式指到 8000
// （与 vite.config.js 的 proxy target 保持一致）；生产同域反代后用相对路径即可。
// 端口写死不理想，但 dev 下后端端口就一个，且 proxy target 也是写死的 8000 ——
// 真要改，两边一起改。
const devtoolsUrl = import.meta.env.DEV
  ? 'http://127.0.0.1:8000/devtools/'
  : '/devtools/'

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
        <!-- ⚠️ dev 下前端(5173)与后端(8000)**不同源**，写 `/devtools/` 会指到 vite 自己，
             被它的 SPA fallback 接住又返回前端页面 —— 死循环，永远打不开真调试台。
             （这个错躺了很久：vite 的 proxy target 是 8000，dev 下指过去就对了；
              生产同域反代时才该用相对路径。）
             另外它已从"主调试入口"降级为**应急通道**：日常功能前端都有，
             它只剩"前端挂了时验证后端还活着"和"万能请求"两个价值。 -->
        <a :href="devtoolsUrl" target="_blank" rel="noopener">应急调试台 →</a>
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
