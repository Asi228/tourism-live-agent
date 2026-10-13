/**
 * 长期记忆「单条开关」的守卫（`node --test`，零依赖 —— 见 package.json 的 test 脚本）。
 *
 * 这块修过一次**用户能直接碰到**的 bug：开关点了不生效、自己弹回去。
 * 根因是"值被翻了两次"—— Element Plus 的 switch 先
 * `emit('update:modelValue', val)`、**然后**才 `emit('change', val)`，
 * 所以进 @change 时 v-model 已写入新值，而旧代码又 `!before` 把它翻回去，
 * PATCH 发出去的是旧值。当时的代码在 .vue 里，任何测试都够不着。
 * 抽出来 + 依赖注入之后，下面这三个契约第一次被钉住。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { toggleMemoryState } from './memoryToggle.js'

/** 记录每次调用参数的假 API。 */
function fakeApi(impl) {
  const calls = []
  return {
    calls,
    update: async (id, patch) => {
      calls.push({ id, patch })
      return impl(id, patch)
    },
  }
}

test('🔴 PATCH 发出去的就是收到的 value，不许自己再翻一次（开关 bug 的根因）', async () => {
  for (const value of [true, false]) {
    const api = fakeApi(() => ({ data: { id: 1, is_enabled: value } }))
    const memory = { id: 1, is_enabled: !value } // v-model 已写入新值的状态
    const list = [memory]

    await toggleMemoryState(memory, value, { api, list })

    assert.deepEqual(
      api.calls,
      [{ id: 1, patch: { is_enabled: value } }],
      `value=${value} 被翻转了 —— 这正是"点了开关自己弹回去"的根因`
    )
  }
})

test('成功时列表项替换为服务端返回的权威值（不是本地猜的值）', async () => {
  const serverRow = { id: 7, is_enabled: true, content: '服务端权威值' }
  const api = fakeApi(() => ({ data: serverRow }))
  const memory = { id: 7, is_enabled: true, content: '本地旧值' }
  const list = [memory, { id: 8, is_enabled: true }]

  await toggleMemoryState(memory, true, { api, list })

  assert.equal(list[0], serverRow)
  assert.equal(list[1].id, 8) // 其它项不动
})

test('失败时回滚为 !value（只有失败才回滚）', async () => {
  const api = fakeApi(() => {
    throw new Error('500')
  })

  // 想开 → 失败 → 回滚为关
  const on = { id: 1, is_enabled: true }
  await toggleMemoryState(on, true, { api, list: [on] })
  assert.equal(on.is_enabled, false)

  // 想关 → 失败 → 回滚为开（两个方向都要验，别只测一边）
  const off = { id: 2, is_enabled: false }
  await toggleMemoryState(off, false, { api, list: [off] })
  assert.equal(off.is_enabled, true)
})

test('list 里找不到那条时安静跳过，不崩也不误改', async () => {
  const api = fakeApi(() => ({ data: { id: 99, is_enabled: true } }))
  const memory = { id: 99, is_enabled: false }
  await toggleMemoryState(memory, true, { api, list: [] })
  assert.equal(memory.is_enabled, false) // 成功路径不碰 memory 本身
})
