/**
 * 长期记忆「单条开关」的状态变更逻辑（从 MemoryPanel.vue 抽出）。
 *
 * 🔴 抽它出来的直接原因：这段逻辑修过一次**用户能直接碰到**的 bug
 * （开关点了不生效），而它当时写在 .vue 里、没有任何测试能碰到。
 * 抽成"依赖可注入"的纯逻辑后，`node --test` 就能把三个契约钉住
 * （见 memoryToggle.test.js）：
 *   1. PATCH 发出去的 value **就是收到的 value**（不许自己再翻一次）；
 *   2. 成功时列表项替换为服务端返回的**权威值**；
 *   3. 失败时回滚为 `!value`。
 *
 * ⚠️ 第 1 条为什么是契约：Element Plus 的 switch 先
 * `emit('update:modelValue', val)`、**然后**才 `emit('change', val)`
 * （源码 switch.vue 的 handleChange，两行紧邻）。
 * 所以 `@change` 触发时 `v-model` 已经把新值写进 `memory.is_enabled` 了 ——
 * 在这个函数里再翻一次就是把值翻回去，PATCH 发旧值，服务端原样返回，
 * 表现是"点了开关自己弹回去"。
 *
 * @param {{id: number, is_enabled: boolean}} memory
 *        列表里的那条（失败时**就地回滚**它的 is_enabled）
 * @param {boolean} value el-switch 传下来的新值
 * @param {{api: object, list: Array}} deps
 *        依赖注入：`api.update(id, patch)` 发请求；`list` 是列表引用
 */
export async function toggleMemoryState(memory, value, { api, list }) {
  try {
    const body = await api.update(memory.id, { is_enabled: value })
    const idx = list.findIndex((m) => m.id === memory.id)
    if (idx >= 0) list[idx] = body.data
  } catch {
    // 只有失败才回滚 —— 成功时以服务端返回的权威值为准
    memory.is_enabled = !value
    /* 拦截器已提示 */
  }
}
