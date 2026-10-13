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
          <div v-for="m in memories" :key="m.id" class="mem-item">
            <span class="mem-text" :title="m.content">{{ m.content }}</span>
            <span class="mem-del" title="删除" @click="removeMemory(m)">×</span>
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
  font-size: 12px;
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
  font-size: 12px;
  line-height: 1.6;
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
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
</style>
