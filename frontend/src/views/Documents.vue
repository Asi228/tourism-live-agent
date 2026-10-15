<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { documentApi, knowledgeApi } from '../api'

const route = useRoute()

const kbList = ref([])
const kbId = ref(null)
const loading = ref(false)
const rows = ref([])

const chunkVisible = ref(false)
const chunkDoc = ref(null)
const chunks = ref([])
const chunkLoading = ref(false)

const STATUS_TEXT = {
  pending: '待处理',
  processing: '处理中',
  completed: '已完成',
  failed: '失败',
}
const STATUS_TYPE = {
  pending: 'info',
  processing: 'warning',
  completed: 'success',
  failed: 'danger',
}

const canWrite = computed(() => {
  const kb = kbList.value.find((k) => k.id === kbId.value)
  if (!kb) return false
  return kb.my_permission === 'write' || kb.my_permission === 'admin'
})

function formatSize(bytes) {
  if (bytes == null) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function loadKbList() {
  const body = await knowledgeApi.list()
  kbList.value = body.data || []
  const fromQuery = Number(route.query.kb)
  if (fromQuery && kbList.value.some((k) => k.id === fromQuery)) kbId.value = fromQuery
  else if (kbList.value.length && !kbId.value) kbId.value = kbList.value[0].id
}

async function loadDocs() {
  if (!kbId.value) {
    rows.value = []
    return
  }
  loading.value = true
  try {
    const body = await documentApi.list(kbId.value)
    rows.value = body.data || []
    schedulePolling()
  } catch {
    rows.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 状态轮询 ----------
// 解析在后台任务里跑，接口返回时状态还是 pending。不轮询的话用户会以为卡住了。
// 只在真的存在未完成文档时才轮询，并且有次数上限，避免后台任务挂掉时无限打接口。
let timer = null
let polls = 0
const MAX_POLLS = 30

function hasPending() {
  return rows.value.some((d) => d.status === 'pending' || d.status === 'processing')
}

function schedulePolling() {
  if (timer) return
  if (!hasPending()) return
  polls = 0
  timer = setInterval(async () => {
    polls += 1
    if (polls > MAX_POLLS) {
      stopPolling()
      return
    }
    try {
      const body = await documentApi.list(kbId.value)
      rows.value = body.data || []
    } catch {
      /* 单次失败忽略，继续下一次 */
    }
    if (!hasPending()) stopPolling()
  }, 2000)
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function onKbChange() {
  stopPolling()
  await loadDocs()
}

// ---------- 上传 ----------
// 用 el-upload 的自定义上传（http-request），因为要走我们自己的 axios 封装
// （自动带 token、统一错误提示），而不是 el-upload 内置的 XHR。

// 选择器的白名单从后端取（.env 的 ALLOWED_EXTENSIONS 是唯一权威）——
// 以前这里硬编码第 4 份清单：改了 .env 之后前端会继续放出一个服务端必拒的格式。
// 兜底值只在接口失败时生效；服务端仍会对文件类型做最终校验。
const uploadAccept = ref('.pdf,.txt,.md,.markdown,.docx')

async function loadUploadConfig() {
  try {
    const body = await documentApi.uploadConfig()
    const exts = body.data?.allowed_extensions
    if (Array.isArray(exts) && exts.length) {
      uploadAccept.value = exts.map((e) => `.${e}`).join(',')
    }
  } catch {
    /* 保持兜底清单，不打断页面 */
  }
}

async function customUpload(options) {
  const file = options.file
  try {
    await documentApi.upload(kbId.value, file)
    ElMessage.success(`「${file.name}」已上传，正在后台解析`)
    await loadDocs()
  } catch {
    /* 拦截器已提示（类型不支持 / 超限 / 无权限…） */
  }
}

// ---------- 操作 ----------
async function vectorizeOne(row) {
  try {
    const body = await documentApi.vectorize(row.id)
    ElMessage.success(`已写入 ${body.data?.vectorized ?? 0} 条向量`)
  } catch {
    /* 拦截器已提示 */
  }
}

async function vectorizeAll() {
  try {
    await ElMessageBox.confirm(
      '会把该知识库下所有「已完成」的文档重新向量化。重复执行是幂等的（不会产生重复向量）。',
      '批量向量化',
      { type: 'info' }
    )
  } catch {
    return
  }
  try {
    const body = await knowledgeApi.vectorize(kbId.value)
    ElMessage.success(`批量向量化完成：写入 ${body.data?.vectorized ?? 0} 条`)
  } catch {
    /* 拦截器已提示 */
  }
}

async function openChunks(row) {
  chunkDoc.value = row
  chunkVisible.value = true
  chunkLoading.value = true
  try {
    const body = await documentApi.chunks(row.id)
    chunks.value = body.data || []
  } catch {
    chunks.value = []
  } finally {
    chunkLoading.value = false
  }
}

async function removeDoc(row) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.filename}」会同时删掉它的切分块、向量和磁盘文件。确定继续？`,
      '提示',
      { type: 'warning', confirmButtonText: '确认删除' }
    )
  } catch {
    return
  }
  try {
    await documentApi.remove(row.id)
    ElMessage.success('已删除')
    await loadDocs()
  } catch {
    /* 拦截器已提示 */
  }
}

onMounted(async () => {
  try {
    await loadUploadConfig()
    await loadKbList()
    await loadDocs()
  } catch {
    /* 拦截器已提示 */
  }
})

onUnmounted(stopPolling)
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-head">
        <div class="left">
          <span>文档</span>
          <el-select v-model="kbId" placeholder="选择知识库" style="width: 220px" @change="onKbChange">
            <el-option v-for="kb in kbList" :key="kb.id" :label="kb.name" :value="kb.id" />
          </el-select>
        </div>
        <div class="right">
          <el-button :loading="loading" @click="loadDocs">刷新</el-button>
          <el-button :disabled="!canWrite" @click="vectorizeAll">批量向量化</el-button>
          <el-upload
            :show-file-list="false"
            :http-request="customUpload"
            :disabled="!canWrite || !kbId"
            :accept="uploadAccept"
          >
            <el-button type="primary" :disabled="!canWrite || !kbId">上传文档</el-button>
          </el-upload>
        </div>
      </div>
    </template>

    <el-alert
      v-if="kbId && !canWrite"
      type="info"
      :closable="false"
      show-icon
      title="你对这个知识库只有只读权限，无法上传或删除文档"
      style="margin-bottom: 12px"
    />

    <el-table v-loading="loading" :data="rows" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
      <el-table-column prop="file_type" label="类型" width="80" />
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tooltip v-if="row.status === 'failed'" :content="row.error_msg || '未知原因'" placement="top">
            <el-tag size="small" type="danger">失败</el-tag>
          </el-tooltip>
          <el-tag v-else size="small" :type="STATUS_TYPE[row.status] || 'info'">
            {{ STATUS_TEXT[row.status] || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="切块" width="80" />
      <el-table-column prop="char_count" label="字符" width="90" />
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="row.chunk_count === 0" @click="openChunks(row)">切块</el-button>
          <el-button
            link
            type="primary"
            :disabled="!canWrite || row.status !== 'completed'"
            @click="vectorizeOne(row)"
          >
            向量化
          </el-button>
          <el-button link type="danger" :disabled="!canWrite" @click="removeDoc(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !rows.length" description="这个知识库还没有文档" />

    <!-- 切块预览 -->
    <el-drawer v-model="chunkVisible" :title="`切块预览 · ${chunkDoc?.filename || ''}`" size="640px">
      <div v-loading="chunkLoading">
        <el-alert
          v-if="chunkDoc?.file_type === 'txt' || chunkDoc?.file_type === 'md' || chunkDoc?.file_type === 'markdown'"
          type="info"
          :closable="false"
          title="纯文本类文档没有页码（page_number 为 null），只有 PDF 才有"
          style="margin-bottom: 12px"
        />
        <div v-for="c in chunks" :key="c.id" class="chunk">
          <div class="chunk-head">
            <span>#{{ c.chunk_index }}</span>
            <span v-if="c.page_number != null">第 {{ c.page_number }} 页</span>
            <span>{{ c.char_count }} 字</span>
          </div>
          <div class="chunk-body">{{ c.content }}</div>
        </div>
        <el-empty v-if="!chunkLoading && !chunks.length" description="没有切块" />
      </div>
    </el-drawer>
  </el-card>
</template>

<style scoped>
.left,
.right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.chunk {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.chunk-head {
  display: flex;
  gap: 14px;
  font-size: var(--el-font-size-extra-small);
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.chunk-body {
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
