<script setup>
/**
 * 会话列表（侧边栏上半部分）—— 从 Chat.vue 切出来的**视图组件**。
 *
 * ⚠️ 切出来的是**视图**，不是逻辑：会话的新建 / 切换 / 删除都要动流式状态
 * （`openConversation` 里得先 `abortStream()`、再重置 messages 与 currentConvId），
 * 所以那些函数留在 Chat.vue。这里只负责把列表画出来、把用户动作抛上去。
 *
 * 这么切的收益是"改排版不必再翻 Chat.vue 那上千行的问答逻辑"；
 * 代价是父组件要多维护一份 props / emits 契约。
 *
 * `formatTime` 只被这块列表用，所以跟着搬过来（不必再当函数 prop 传）。
 */

defineProps({
  conversations: { type: Array, default: () => [] },
  currentId: { type: [Number, String], default: null },
  loading: { type: Boolean, default: false },
  hasMore: { type: Boolean, default: false },
})

const emit = defineEmits(['new', 'select', 'remove', 'loadMore'])

function formatTime(s) {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : `${d.getMonth() + 1}/${d.getDate()}`
}

</script>

<template>
  <el-button type="primary" class="new-btn" @click="emit('new')">＋ 新对话</el-button>
  <div class="conv-list">
    <div
      v-for="c in conversations"
      :key="c.id"
      class="conv-item"
      :class="{ active: c.id === currentId }"
      @click="emit('select', c.id)"
    >
      <div class="conv-title">{{ c.title }}</div>
      <div class="conv-meta">
        <span>{{ c.message_count }} 条 · {{ formatTime(c.updated_at) }}</span>
        <span class="conv-del" title="删除" @click.stop="emit('remove', c)">×</span>
      </div>
    </div>
    <div v-if="!conversations.length" class="conv-empty">还没有对话，直接在右边提问即可</div>
    <el-button
      v-if="hasMore"
      text
      size="small"
      class="conv-more"
      :loading="loading"
      @click="emit('loadMore')"
    >加载更多</el-button>
  </div>

</template>

<style scoped>
.new-btn {
  width: 100%;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  /* 会话多起来时它必须能被压缩 —— flex 子项默认 min-height:auto 会拒绝 */
  min-height: 0;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 6px;
}
.conv-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
}
.conv-item:hover {
  background: var(--el-fill-color-light);
}
.conv-item.active {
  background: var(--el-color-primary-light-9);
}
.conv-title {
  font-size: 13px;
  line-height: 1.4;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.conv-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.conv-del {
  padding: 0 4px;
  border-radius: 3px;
  visibility: hidden;
}
.conv-item:hover .conv-del {
  visibility: visible;
}
.conv-del:hover {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.conv-empty {
  padding: 12px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}

.conv-more {
  width: 100%;
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}
</style>
