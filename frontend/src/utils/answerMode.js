/**
 * 「答案性质」（后端 `answer_mode`）的**唯一展示口径**。
 *
 * 从 Chat.vue 抽出来的理由：它是纯映射，抽出来才能被 `node --test` 直接跑
 * （见 answerMode.test.js）—— 而"后端加了新取值、前端没补映射"这类漂移
 * 只有测试能钉住，`vite build` 永远看不见（它只验证语法和引用，不验证行为）。
 */

// 后端 `answer_mode` 的取值 → 标签样式与话术。
// **这张表是"答案性质"的唯一展示口径** —— 后端加了新取值而这里没补，
// `answerModeTag` 会退化成显示原始英文串（`v-if` 仍在，不会变成空白）。
export const ANSWER_MODE = {
  grounded: { text: '依据资料', type: 'success' },
  no_evidence: { text: '无可用资料', type: 'warning' },
  smalltalk: { text: '寒暄直答', type: 'info' },
  general: { text: '通用模型回答', type: 'primary' },
  tool: { text: '实时工具结果', type: 'success' },
  // 命中知识库常见问题：答案来自管理端写的标准答案，**不是**模型生成的，
  // 所以既不是"依据资料"（没引文档）也不是"无可用资料"（有依据，只是依据是 FAQ）。
  faq: { text: '来自常见问题', type: 'primary' },
  // 文档清单："有哪些文档"这类目录型问题的答案（系统元数据直接拼出，未经模型）。
  // 同样不是"依据资料"（没引正文）——它是"目录"。
  catalog: { text: '文档清单', type: 'info' },
}

/**
 * 取一个取值对应的展示形态 `{ text, type }`。
 *
 * **未知取值降级显示原始串**：后端哪天加了第五种 mode，这里立刻把
 * `xxx` 原样显示出来（用户看得见、开发者也看得见），比"标签消失"
 * 或"渲染成 undefined"好排查得多。
 *
 * 用 `Object.hasOwn` 而不是 `ANSWER_MODE[mode]` 直取：后者在 `mode` 恰好是
 * `"constructor"` 这类原型键时会取到一个**函数**（truthy）—— 后端真吐这种值
 * 的概率极低，但一行 `hasOwn` 就能把这个洞焊死，顺手做掉。
 */
export function answerModeTag(mode) {
  if (Object.hasOwn(ANSWER_MODE, mode)) return ANSWER_MODE[mode]
  return { text: mode, type: 'info' }
}
