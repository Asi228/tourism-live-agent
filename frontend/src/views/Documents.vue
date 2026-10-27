<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { commerceApi, documentApi, knowledgeApi } from '../api'

const route = useRoute()

const kbList = ref([])
const kbId = ref(null)
const loading = ref(false)
const rows = ref([])
const products = ref([])
const documentTypes = ref([])
const filters = reactive({ product_id: null, document_type: '', active_only: false })

const chunkVisible = ref(false)
const chunkDoc = ref(null)
const chunks = ref([])
const chunkLoading = ref(false)
const uploadVisible = ref(false)
const uploadFile = ref(null)
const uploading = ref(false)
const uploadMeta = reactive({
  product_id: null,
  sku_id: null,
  document_type: 'general',
  version: 'v1',
  effective_from: null,
  effective_to: null,
})

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

const uploadSkuOptions = computed(() => {
  const product = products.value.find((p) => p.id === uploadMeta.product_id)
  return product?.skus || []
})

const productMap = computed(() =>
  Object.fromEntries(products.value.map((product) => [product.id, product])),
)

const documentTypeMap = computed(() =>
  Object.fromEntries(documentTypes.value.map((item) => [item.value, item.label])),
)

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
    const body = await documentApi.list(kbId.value, {
      ...(filters.product_id ? { product_id: filters.product_id } : {}),
      ...(filters.document_type ? { document_type: filters.document_type } : {}),
      ...(filters.active_only ? { active_only: true } : {}),
    })
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
      const body = await documentApi.list(kbId.value, {
        ...(filters.product_id ? { product_id: filters.product_id } : {}),
        ...(filters.document_type ? { document_type: filters.document_type } : {}),
        ...(filters.active_only ? { active_only: true } : {}),
      })
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
    if (Array.isArray(body.data?.document_types)) {
      documentTypes.value = body.data.document_types
    }
  } catch {
    /* 保持兜底清单，不打断页面 */
  }
}

function openUpload() {
  uploadFile.value = null
  Object.assign(uploadMeta, {
    product_id: null,
    sku_id: null,
    document_type: 'general',
    version: 'v1',
    effective_from: null,
    effective_to: null,
  })
  uploadVisible.value = true
}

function onUploadFileChange(file) {
  uploadFile.value = file.raw || null
}

function onUploadProductChange() {
  uploadMeta.sku_id = null
}

async function submitUpload() {
  const file = uploadFile.value
  if (!file) {
    ElMessage.warning('请选择要上传的文件')
    return
  }
  if (!uploadMeta.version?.trim()) {
    ElMessage.warning('请填写资料版本')
    return
  }
  uploading.value = true
  try {
    await documentApi.upload(kbId.value, file, {
      ...uploadMeta,
      version: uploadMeta.version.trim(),
    })
    ElMessage.success(`「${file.name}」已上传，正在后台解析`)
    uploadVisible.value = false
    await loadDocs()
  } catch {
    /* 拦截器已提示（类型不支持 / 超限 / 无权限…） */
  } finally {
    uploading.value = false
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
    await Promise.all([
      loadUploadConfig(),
      commerceApi.products().then((body) => { products.value = body.data || [] }),
    ])
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
          <el-button type="primary" :disabled="!canWrite || !kbId" @click="openUpload">
            上传业务资料
          </el-button>
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

    <div class="filters">
      <el-select
        v-model="filters.product_id" clearable filterable placeholder="全部商品"
        style="width: 220px" @change="loadDocs"
      >
        <el-option v-for="product in products" :key="product.id" :label="product.name" :value="product.id" />
      </el-select>
      <el-select
        v-model="filters.document_type" clearable placeholder="全部资料类型"
        style="width: 160px" @change="loadDocs"
      >
        <el-option
          v-for="item in documentTypes" :key="item.value" :label="item.label" :value="item.value"
        />
      </el-select>
      <el-switch
        v-model="filters.active_only" inline-prompt active-text="当前有效" inactive-text="全部时期"
        style="--el-switch-on-color: var(--el-color-success)" @change="loadDocs"
      />
    </div>

    <el-table v-loading="loading" :data="rows" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
      <el-table-column prop="file_type" label="类型" width="80" />
      <el-table-column label="业务资料" min-width="150">
        <template #default="{ row }">
          <div class="business-meta">
            <span>{{ documentTypeMap[row.document_type] || row.document_type }}</span>
            <small>{{ row.version }}</small>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="关联范围" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <template v-if="row.product_id">
            {{ productMap[row.product_id]?.name || `商品 #${row.product_id}` }}
            <span v-if="row.sku_id" class="muted"> · SKU #{{ row.sku_id }}</span>
          </template>
          <span v-else class="muted">通用资料</span>
        </template>
      </el-table-column>
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

    <el-dialog v-model="uploadVisible" title="上传业务资料" width="620px">
      <el-alert
        type="info" :closable="false" show-icon
        title="商品说明和宣传依据必须关联商品；平台规则、直播规范等可以作为通用资料。"
        style="margin-bottom: 16px"
      />
      <el-form label-position="top">
        <el-form-item label="资料文件" required>
          <el-upload
            :auto-upload="false" :limit="1" :accept="uploadAccept"
            :on-change="onUploadFileChange" :on-remove="() => { uploadFile = null }"
          >
            <el-button>选择文件</el-button>
            <template #tip><div class="el-upload__tip">允许格式：{{ uploadAccept }}</div></template>
          </el-upload>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="资料类型" required>
            <el-select v-model="uploadMeta.document_type" style="width: 100%">
              <el-option
                v-for="item in documentTypes" :key="item.value" :label="item.label" :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="资料版本" required>
            <el-input v-model="uploadMeta.version" maxlength="32" placeholder="例如 2026.10 或 v2" />
          </el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item label="关联商品">
            <el-select
              v-model="uploadMeta.product_id" clearable filterable placeholder="不关联商品"
              style="width: 100%" @change="onUploadProductChange"
            >
              <el-option
                v-for="product in products" :key="product.id" :label="product.name" :value="product.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="关联 SKU">
            <el-select
              v-model="uploadMeta.sku_id" clearable filterable :disabled="!uploadMeta.product_id"
              placeholder="适用于整个商品" style="width: 100%"
            >
              <el-option
                v-for="sku in uploadSkuOptions" :key="sku.id"
                :label="`${sku.specification}（${sku.sku_code}）`" :value="sku.id"
              />
            </el-select>
          </el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item label="生效时间">
            <el-date-picker
              v-model="uploadMeta.effective_from" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="立即生效" style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="失效时间">
            <el-date-picker
              v-model="uploadMeta.effective_to" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="长期有效" style="width: 100%"
            />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传并解析</el-button>
      </template>
    </el-dialog>

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
.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.business-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.business-meta small,
.muted {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
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
@media (max-width: 780px) {
  .form-grid { grid-template-columns: 1fr; gap: 0; }
  .filters { align-items: stretch; flex-direction: column; }
}
</style>
