"""RAG 的提示词模板与上下文拼装。

本文件是纯函数，不碰网络也不碰数据库，所以能单独测 —— 这是刻意的：
「上下文怎么拼、超长怎么截」直接决定答案质量和 token 成本，值得有测试盯着。
"""

import logging

logger = logging.getLogger(__name__)

# 系统提示词。四条硬规则，按重要性排序：
#   1. 只用资料回答 —— 企业知识库的全部价值就在这里。让模型自由发挥，
#      员工会把通用常识当成公司规定（比如"年假一般 5 天"），危害比答不出来更大。
#   2. 标注来源编号 —— 编号由**我们**生成（见 build_context），不让模型自己写文件名，
#      否则它会编出"《员工手册》第 3 章"这种看起来很真、实际不存在的东西。
#   3. 资料不足就明说，不要编。
SYSTEM_PROMPT = """你是企业知识库的问答助手。你只能依据用户提供的「参考资料」回答问题。

必须遵守的规则：
1. 只使用参考资料中的内容作答。不要使用你自己的知识补充，也不要凭常理推断。
2. 每一条结论后面都要标注来源编号，例如 [1]；引用多条就写 [1][3]。
3. 如果参考资料不足以回答问题，就直接说明「根据现有资料无法回答该问题」，
   并简要说明还缺少什么信息。绝对不要编造。
4. 用简洁的中文作答，能分点就分点，不要复述这些规则。
"""

NO_HIT_ANSWER = (
    "知识库中没有找到与该问题相关的内容。"
    "请确认相关文档已上传并完成向量化，或换一种问法再试。"
)


def _page_text(page_number: int | None) -> str:
    """页码后缀。纯文本文档（txt/md）没有页码（用 None 表示），此时不显示。

    判 `is not None` 而不是判真假：万一将来出现合法的「第 0 页」，
    用 `if page_number` 会把页码静默吞掉 —— 引用里少个页码，很难被发现。
    """
    return f"（第 {page_number} 页）" if page_number is not None else ""


def build_context(results: list[dict], max_chars: int) -> tuple[str, list[dict]]:
    """把检索结果拼成带编号的上下文。

    返回 `(上下文文本, 实际采用的结果列表)`。

    **两个返回值必须一起用**：编号是按下标生成的（[1]、[2]…），
    所以 sources 只能用 adopted，不能用原始 results —— 否则被丢掉的那几条
    会让编号和引用错位，模型写的 [2] 就对不上了。

    截断策略：结果已按相似度降序，所以**从低分的开始丢**（放不下就 break），
    高分内容一定进得去。只有"第一条就超长"这种极端情况才会截断正文本身。
    """
    adopted: list[dict] = []
    blocks: list[str] = []
    used = 0

    for item in results:
        content = (item.get("content") or "").strip()
        if not content:
            continue

        index = len(adopted) + 1
        header = "[%d] 来源：%s%s" % (
            index,
            item.get("document_name") or "(未知文档)",
            _page_text(item.get("page_number")),
        )
        block = header + "\n" + content

        if used + len(block) + 2 > max_chars:
            if adopted:
                # 放不下了，且已有内容可用 → 后面更低分的全部丢弃
                logger.info(
                    "上下文超限，丢弃低分结果：采用 %s 条 / 共 %s 条，已用 %s 字",
                    len(adopted), len(results), used,
                )
                break
            # 第一条就超长：截断它的正文，保证至少有一段资料可答
            room = max_chars - used - len(header) - 1
            if room <= 0:
                break
            block = header + "\n" + content[:room]

        blocks.append(block)
        adopted.append(item)
        used += len(block) + 2

    return "\n\n".join(blocks), adopted


def build_user_message(query: str, context: str) -> str:
    """提问消息。资料在前、问题在后 —— 长上下文放前面更利于模型抓重点。"""
    return (
        "参考资料：\n"
        f"{context}\n\n"
        f"问题：{query}\n\n"
        "请依据以上参考资料回答，并标注来源编号。"
    )
