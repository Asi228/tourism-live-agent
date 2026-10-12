<script setup>
/**
 * 概览页 —— 登录后的第一屏。
 *
 * 三条口径（都由后端 `stats_service` 决定，前端只负责展示）：
 *  1. 数字按**当前用户可见范围**算，不是全库 —— 员工看到的是他有权访问的那部分；
 *  2. 问答数只算**自己的**；
 *  3. 管理员多一块系统级指标（用户数 / 向量条数），非管理员拿到的 `admin` 是
 *     **null**，此时整块不渲染 —— 而不是显示"用户 0 人"这种假数字。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { statsApi } from '../api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const user = useUserStore()

const loading = ref(false)
const data = ref(null)

const DOC_STATUS = {
  completed: { text: '已完成', type: 'success' },
  processing: { text: '处理中', type: 'warning' },
  pending: { text: '待处理', type: 'info' },
  failed: { text: '失败', type: 'danger' },
}

// 文档按状态拆开显示，空状态不给（否则四个数字里三个是 0，反而看不出重点）
const docStats = computed(() => {
  const by = data.value?.documents?.by_status || {}
  return Object.keys(DOC_STATUS)
    .filter((k) => by[k])
    .map((k) => ({ key: k, ...DOC_STATUS[k], count: by[k] }))
})

async function load() {
  loading.value = true
  try {
    const body = await statsApi.overview()
    data.value = body.data
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="dash">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>{{ user.displayName }}，欢迎回来</span>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>

      <template v-if="data">
        <div class="metrics">
          <div class="metric" @click="router.push('/knowledge')">
            <div class="num">{{ data.scope.kb_count }}</div>
            <div class="label">
              知识库
              <span v-if="data.scope.unlimited" class="sub">（你是不受限管理员）</span>
            </div>
          </div>
          <div class="metric" @click="router.push('/documents')">
            <div class="num">{{ data.documents.total }}</div>
            <div class="label">文档</div>
          </div>
          <div class="metric">
            <div class="num">{{ data.questions.total }}</div>
            <div class="label">我的提问（累计）</div>
          </div>
          <div class="metric">
            <div class="num">{{ data.questions.today }}</div>
            <div class="label">我的提问（今日）</div>
          </div>
          <div v-if="data.admin" class="metric">
            <div class="num">{{ data.admin.user_count }}</div>
            <div class="label">系统用户</div>
          </div>
          <div v-if="data.admin" class="metric">
            <div class="num">{{ data.admin.vector_count ?? '—' }}</div>
            <div class="label">
              向量条数
              <span v-if="data.admin.vector_count === null" class="sub">（Milvus 不可用）</span>
            </div>
          </div>
        </div>

        <div v-if="docStats.length" class="status-row">
          <span class="status-title">文档状态：</span>
          <el-tag v-for="s in docStats" :key="s.key" :type="s.type" size="small" class="status-tag">
            {{ s.text }} {{ s.count }}
          </el-tag>
        </div>
      </template>
    </el-card>

    <div class="cols">
      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span>最近文档</span>
            <el-button size="small" text type="primary" @click="router.push('/documents')">全部 →</el-button>
          </div>
        </template>
        <el-empty v-if="!data?.recent_documents?.length" description="还没有文档" :image-size="60" />
        <el-table v-else :data="data.recent_documents" size="small">
          <el-table-column prop="filename" label="文件名" show-overflow-tooltip />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="(DOC_STATUS[row.status] || {}).type || 'info'">
                {{ (DOC_STATUS[row.status] || {}).text || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="块" width="60" />
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span>最近提问</span>
            <el-button size="small" text type="primary" @click="router.push('/chat')">去问答 →</el-button>
          </div>
        </template>
        <el-empty v-if="!data?.recent_questions?.length" description="还没有提问" :image-size="60" />
        <div v-else class="q-list">
          <div
            v-for="(q, i) in data.recent_questions"
            :key="i"
            class="q-item"
            @click="router.push('/chat')"
          >
            <div class="q-text">{{ q.query }}</div>
            <div class="q-meta">来自「{{ q.conversation_title }}」</div>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
}
.metric {
  padding: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  text-align: center;
  cursor: pointer;
  transition: border-color .15s;
}
.metric:hover {
  border-color: var(--el-color-primary-light-5);
}
.num {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.3;
}
.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.sub {
  font-size: 11px;
}
.status-row {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.status-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.status-tag {
  margin-right: 2px;
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
@media (max-width: 1000px) {
  .cols { grid-template-columns: 1fr; }
}
.q-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
}
.q-item:last-child {
  border-bottom: none;
}
.q-text {
  font-size: 13px;
  line-height: 1.5;
}
.q-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
</style>
