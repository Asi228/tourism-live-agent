<template>
  <div class="search-debug">
    <el-card>
      <template #header>
        <span>检索调试</span>
        <span class="hint">只检索、不调模型 —— 看双路召回与融合排序的原始结果</span>
      </template>

      <el-form :inline="true" @submit.prevent="run">
        <el-form-item label="查询">
          <el-input
            v-model="form.query"
            placeholder="如：年假几天 / ISO27001"
            style="width: 280px"
            @keyup.enter="run"
          />
        </el-form-item>
        <el-form-item label="范围">
          <el-select
            v-model="form.kb_ids"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="不选 = 全部可访问的库"
            style="width: 260px"
          >
            <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="条数">
          <el-input-number v-model="form.top_k" :min="1" :max="50" style="width: 110px" />
        </el-form-item>
        <el-form-item label="重排">
          <el-switch v-model="form.rerank" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="run">检索</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="summary"
        :title="summary"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
      />

      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column type="index" label="#" width="56" />
        <el-table-column label="文档" min-width="180">
          <template #default="{ row }">
            <div class="doc">{{ row.document_name }}</div>
            <div class="meta">块 #{{ row.chunk_id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="pathTagType(row.retrieval_path)">
              {{ pathLabel(row.retrieval_path) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分数" width="150">
          <template #default="{ row }">
            {{ scoreText(row.score, row.retrieval_path) }}
            <span v-if="row.vector_score != null" class="meta">
              向量 {{ (row.vector_score * 100).toFixed(1) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column label="页码" width="70">
          <template #default="{ row }">{{ row.page_number ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="正文摘要" min-width="320">
          <template #default="{ row }">
            <div class="snippet">{{ row.snippet }}</div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && ran && !rows.length" description="没有命中" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { chatApi, knowledgeApi } from '../api'
import { sourceScoreText } from '../utils/exportMarkdown'

/**
 * 检索调试页（仅管理员，路由 meta.admin 拦着）。
 *
 * 它替代的是过去"开 devtools 单文件页手动 POST /api/search"的做法：
 * 调检索是日常排查的高频动作（关键词路没生效？某文档没进索引？重排前后差多少？），
 * 值得一个正式入口。devtools 保留 —— 它是"万能请求"调试台，还有别的用途。
 *
 * 展示口径与 Chat.vue 的引用卡片、devtools 三处一致：
 *   · 仅关键词路命中的分数是**词项覆盖率**（不是余弦相似度）——
 *     后端用 retrieval_path 标出来源，这里据此换文案，不混叫"相似度"；
 *   · rerank 之后 score 是相关性分，原相似度在 vector_score 里。
 */

const form = reactive({ query: '', kb_ids: [], top_k: 10, rerank: false })
const kbList = ref([])
const rows = ref([])
const loading = ref(false)
const ran = ref(false)
const summary = ref('')

onMounted(async () => {
  try {
    const body = await knowledgeApi.list()
    kbList.value = body.data || []
  } catch {
    /* 拦截器已提示 */
  }
})

function pathLabel(p) {
  return { vector: '向量', both: '双路命中', keyword: '关键词' }[p] || p || '—'
}
function pathTagType(p) {
  return { vector: '', both: 'success', keyword: 'warning' }[p] || 'info'
}
// 与 exportMarkdown.sourceScoreText 同一口径：仅关键词 → 覆盖率文案
function scoreText(score, path) {
  if (score == null) return '—'
  return path === 'keyword'
    ? `词项覆盖率 ${(score * 100).toFixed(1)}%`
    : sourceScoreText(score)
}

async function run() {
  const q = form.query.trim()
  if (!q) {
    ElMessage.warning('先输入查询词')
    return
  }
  loading.value = true
  try {
    // ⚠️ 没选库时不传 kb_ids（空数组 = 范围为空，会什么都搜不到）—— 与 Chat.vue 同口径
    const payload = { query: q, top_k: form.top_k, rerank: form.rerank }
    if (form.kb_ids.length) payload.kb_ids = form.kb_ids
    const body = await chatApi.search(payload)
    const d = body.data
    rows.value = d.results || []
    ran.value = true
    const scope = Array.isArray(d.searched_kb_ids)
      ? `[${d.searched_kb_ids.join(', ')}]`
      : d.searched_kb_ids === null ? '不受限（管理员）' : '—'
    summary.value =
      `命中 ${rows.value.length} 条　候选 ${d.candidate_k ?? '—'}　`
      + `检索范围 ${scope}　`
      + (d.reranked ? '已重排' : `未重排${d.rerank_skipped_reason ? `（${d.rerank_skipped_reason}）` : ''}`)
      + (d.skipped_orphans ? `　跳过孤儿 ${d.skipped_orphans}` : '')
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.doc {
  font-weight: 500;
}
.meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.snippet {
  font-size: 13px;
  white-space: pre-wrap;
}
</style>
