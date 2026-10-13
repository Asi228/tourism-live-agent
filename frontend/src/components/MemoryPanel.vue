<script setup>
/**
 * 长期记忆面板（Phase 11）—— 从 Chat.vue 切出来的。
 *
 * 与会话内的"上文"不是一回事：它是**跨会话**仍然成立的事实（"我在财务部"）。
 * 写入必须显式（后端不做自动提炼），所以这里只有一个输入框 + 列表 + 删除。
 *
 * ⚠️ 「记住」写进去就存下了，但**会不会拼进提示词**由服务端 `USER_MEMORY_ENABLED`
 * 决定 —— 那个开关只管"拼不拼"，不拦写入（README「长期记忆」那节写了这个口径）。
 *
 * 切出来的理由：它在 Chat.vue 里是**完全独立**的一块（只有初始化那一行调它的
 * `loadMemories`），不碰流式状态、不碰会话状态。现在自己负责拉数据。
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { memoryApi } from '../api'

// ---- 长期记忆（Phase 11）----
// 与会话内的"上文"不是一回事：它是**跨会话**仍然成立的事实（"我在财务部"）。
// 写入必须显式（后端不做自动提炼），所以这里只给一个输入框 + 列表 + 删除。
const memories = ref([])
const memDraft = ref('')
const memSaving = ref(false)
// ==================== 长期记忆 ====================

async function loadMemories() {
  try {
    const body = await memoryApi.list()
    memories.value = body.data || []
  } catch {
    memories.value = []
  }
}

async function addMemory() {
  const text = memDraft.value.trim()
  if (!text || memSaving.value) return
  memSaving.value = true
  try {
    const body = await memoryApi.create(text)
    // 后端按"最近更新在前"返回，新写的排最前，与列表顺序一致
    memories.value = [body.data, ...memories.value]
    memDraft.value = ''
    ElMessage.success('已记住')
  } catch {
    /* 拦截器已提示 */
  } finally {
    memSaving.value = false
  }
}

async function removeMemory(memory) {
  try {
    await ElMessageBox.confirm('删除这条长期记忆？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await memoryApi.remove(memory.id)
    memories.value = memories.value.filter((m) => m.id !== memory.id)
  } catch {
    /* 拦截器已提示 */
  }
}

// ---- 编辑 / 单条开关 ----
// 关掉一条只是"暂时不拼进提示词"，数据还在列表里，随时能开回来 ——
// 这和删除是两回事（删了不可逆），所以界面上也要分成两个不同的操作。
const editingId = ref(null)
const editDraft = ref('')

function startEdit(memory) {
  editingId.value = memory.id
  editDraft.value = memory.content
}

function cancelEdit() {
  editingId.value = null
  editDraft.value = ''
}

async function saveEdit(memory) {
  const text = editDraft.value.trim()
  if (!text || text === memory.content) {
    cancelEdit()
    return
  }
  try {
    const body = await memoryApi.update(memory.id, { content: text })
    // 后端按"最近更新在前"返回，改完这条会跳到最前 —— 直接替换原地内容更符合直觉
    const idx = memories.value.findIndex((m) => m.id === memory.id)
    if (idx >= 0) memories.value[idx] = body.data
    cancelEdit()
    ElMessage.success('已更新')
  } catch {
    /* 拦截器已提示 */
  }
}

async function toggleMemory(memory) {
  // 先本地翻转，失败再翻回来：开关是最频繁的操作，等一轮往返会让点击发涩
  const before = memory.is_enabled
  memory.is_enabled = !before
  try {
    const body = await memoryApi.update(memory.id, { is_enabled: memory.is_enabled })
    const idx = memories.value.findIndex((m) => m.id === memory.id)
    if (idx >= 0) memories.value[idx] = body.data
  } catch {
    memory.is_enabled = before
    /* 拦截器已提示 */
  }
}

onMounted(loadMemories)
</script>

<template>
      <!-- 长期记忆（跨会话）。写进去就存下了；会不会拼进提示词由服务端开关决定。 -->
      <div class="mem-block">
        <div class="mem-head">
          <span>我的记忆</span>
          <span class="mem-count" v-if="memories.length">{{ memories.length }}</span>
        </div>
        <div class="mem-add">
          <el-input
            v-model="memDraft"
            size="small"
            placeholder="记住一件事，如：我在财务部"
            maxlength="200"
            @keyup.enter="addMemory"
          />
          <el-button
            size="small"
            :disabled="!memDraft.trim()"
            :loading="memSaving"
            @click="addMemory"
          >记住</el-button>
        </div>
        <div class="mem-list">
          <div v-for="m in memories" :key="m.id" class="mem-item" :class="{ off: !m.is_enabled }">
            <!-- 编辑态：行内输入框，Enter 保存 / Esc 取消 -->
            <el-input
              v-if="editingId === m.id"
              v-model="editDraft"
              size="small"
              maxlength="2000"
              @keyup.enter="saveEdit(m)"
              @keyup.esc="cancelEdit()"
            />
            <span v-else class="mem-text" :title="m.content">{{ m.content }}</span>

            <span class="mem-ops">
              <!-- 单条开关：只影响拼不拼进提示词，数据仍在 -->
              <el-switch
                v-model="m.is_enabled"
                size="small"
                title="关闭后这条暂时不拼进提示词，数据仍保留"
                @change="toggleMemory(m)"
              />
              <template v-if="editingId === m.id">
                <span class="mem-op" title="保存" @click="saveEdit(m)">✓</span>
                <span class="mem-op" title="取消" @click="cancelEdit()">✕</span>
              </template>
              <template v-else>
                <span class="mem-op" title="编辑" @click="startEdit(m)">✎</span>
                <span class="mem-op danger" title="删除" @click="removeMemory(m)">×</span>
              </template>
            </span>
          </div>
          <div v-if="!memories.length" class="mem-empty">
            还没有记忆。写一条会在后续问答里作为背景带上（需服务端开启长期记忆开关）
          </div>
        </div>
      </div>
</template>

<style scoped>
.mem-block {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 8px;
}

.mem-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--el-font-size-extra-small);
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.mem-count {
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.mem-add {
  display: flex;
  gap: 6px;
}

.mem-list {
  max-height: 160px;
  overflow-y: auto;
}

.mem-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 2px;
  font-size: var(--el-font-size-extra-small);
  line-height: 1.6;
}

/* 被关掉的记忆：置灰 + 删除线，一眼看出"这条没在用" */
.mem-item.off .mem-text {
  color: var(--el-text-color-disabled);
  text-decoration: line-through;
}

/* 行内的操作区（开关 / 编辑 / 删除）。默认隐藏， hover 整行才出现 ——
   否则每条记忆右边挂三个图标，列表会显得很吵。 */
.mem-ops {
  flex: none;
  display: flex;
  align-items: center;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.15s;
}

.mem-item:hover .mem-ops {
  opacity: 1;
}

/* 开关不随 hover 隐藏：它是状态指示，常亮才能一眼看出哪条关着 */
.mem-ops :deep(.el-switch) {
  opacity: 1;
}

.mem-op {
  cursor: pointer;
  color: var(--el-text-color-secondary);
  padding: 0 2px;
}

.mem-op:hover {
  color: var(--el-color-primary);
}

.mem-op.danger:hover {
  color: var(--el-color-danger);
}

/* 记忆可能很长，列表里最多显示两行；完整内容靠 title 悬浮看 */
.mem-text {
  flex: 1;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}

.mem-del {
  flex: none;
  cursor: pointer;
  color: var(--el-text-color-secondary);
}

.mem-del:hover {
  color: var(--el-color-danger);
}
.mem-empty {
  padding: 6px 2px;
  font-size: var(--el-font-size-extra-small);
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
</style>
