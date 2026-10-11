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

# 长期记忆段的表头（Phase 11）。措辞刻意与「参考资料」划清界限：
# 记忆是"关于用户的背景"，不是公司制度原文 —— 模型不能拿它当依据去标注来源编号，
# 否则会出现"答案引用了 [1]，但 [1] 其实是用户自己说过的一句话"这种错。
MEMORY_HEADER = (
    "以下是一些关于当前用户的已知信息（仅用于理解其背景，"
    "不能当作公司制度依据，也不要为它们标注来源编号）："
)

# 长对话的滚动摘要段表头（Phase 12）。同样是"背景"，不是资料 —— 与记忆段一个口径：
# 摘要里的结论来自**之前轮次已经答过的内容**，模型不能拿它当本轮的依据去标编号，
# 更不能用它替代本轮检索（资料可能已经更新，以本轮检索为准）。
SUMMARY_HEADER = (
    "以下是这段对话**较早轮次**的摘要（供你了解上下文，"
    "不能当作本轮回答的依据，也不要为它标注来源编号；若与本轮参考资料冲突，以参考资料为准）："
)

# 生成滚动摘要时用的系统提示词。三条要求都是为了"压缩后仍然可用"：
# 保留数字与结论（否则摘要等于删掉了答案）、不加自己的知识（否则摘要会变成幻觉的源头）、
# 与已有摘要合并（否则会攒成一串越来越长的分段摘要）。
SUMMARY_SYSTEM = """你在为一段企业知识库问答对话做滚动摘要，供后续轮次当背景参考。

要求：
1. 保留**具体事实与数字**（天数、金额、期限、比例、流程名称），这些是后续轮次真正要用的。
2. 只总结对话里出现过的内容。**不要添加你自己的知识，也不要推断没说过的事**。
3. 给了「已有摘要」就把它和新增对话**合并成一段**，不要写成两段或列表。
4. 中文，第三人称陈述，控制在 200 字以内。不要复述这些要求。
5. 对话里"知识库中没有找到相关内容"这类轮次，要写成**「用户问过 X，当时资料库没有」**
   这样的陈述，不要把当时的答复当成已确认的结论 —— 那样后续轮次会误以为它有依据。
"""


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


def _snippet(text: str, limit: int) -> str:
    """引用摘要：把换行压平，再按配置长度截断。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def build_sources(adopted: list[dict], snippet_chars: int | None = None) -> list[dict]:
    """把「实际采用的资料」转成对外的引用列表。

    **必须传 adopted，不能传原始 results** —— 编号是按下标生成的（[1]、[2]…），
    被上下文截掉的那几条会让编号与引用错位：模型写的 [2] 会指到别的文档。
    """
    if snippet_chars is None:
        from app.config import settings

        snippet_chars = settings.RAG_SNIPPET_CHARS

    return [
        {
            "index": index,
            "chunk_id": item["chunk_id"],
            "document_id": item["document_id"],
            "document_name": item["document_name"],
            "knowledge_base_id": item["knowledge_base_id"],
            "page_number": item.get("page_number"),
            "score": item["score"],
            "snippet": _snippet(item.get("content") or "", snippet_chars),
        }
        for index, item in enumerate(adopted, start=1)
    ]


def build_prompt_and_sources(
    results: list[dict], max_chars: int, snippet_chars: int | None = None
) -> tuple[str, list[dict], list[dict]]:
    """拼上下文 + 生成引用列表 —— **全项目只此一份**。

    返回 `(上下文文本, 实际采用的资料, 引用列表)`。`adopted` 为空表示一条都拼不进去
    （配置过小，或命中的正文全是空白），此时**绝不能去调大模型** —— 那是拿空资料作答。

    为什么抽成一个函数：这段逻辑原先在 `rag_service`（纯 RAG）和 `agent/graph`（Agent）
    各写了一遍。它同时决定「引用编号对不对」和「会不会拿空资料去调模型」，
    两处实现必然改一处忘一处 —— 这正是 `agent/tools.py` 头注释里说要避免的那类问题。
    """
    context, adopted = build_context(results, max_chars)
    return context, adopted, build_sources(adopted, snippet_chars)


def build_memory_section(memories: list[str], max_chars: int) -> str:
    """把长期记忆拼成系统提示词里的一段（Phase 11）。**没有可用记忆时返回空串。**

    截断方向与 `build_context` 一致：传入的列表按「最近更新的在前」，
    放不下时**从最旧的开始丢**，保证新事实一定进得去。
    `max_chars <= 0` 表示不要记忆（与 `CONVERSATION_MAX_HISTORY_TURNS=0` 同一个习惯）。
    """
    if max_chars <= 0:
        return ""

    lines: list[str] = []
    used = 0
    for item in memories:
        text = " ".join((item or "").split())   # 压平换行：一条记忆占一行，好读也好截
        if not text:
            continue
        line = f"- {text}"
        if used + len(line) + 1 > max_chars:
            if lines:
                logger.info(
                    "长期记忆超限，丢弃较旧的条目：采用 %s 条 / 共 %s 条，已用 %s 字",
                    len(lines), len(memories), used,
                )
                break
            line = line[:max_chars]   # 第一条就超长：截断它，保证至少有一条记忆可用
        lines.append(line)
        used += len(line) + 1

    if not lines:
        return ""
    return MEMORY_HEADER + "\n" + "\n".join(lines)


def build_summary_section(summary: str | None, max_chars: int) -> str:
    """把长对话的滚动摘要拼成系统提示词里的一段（Phase 12）。没有摘要时返回空串。

    截断方向与另外两个上下文函数**刻意相反**：这里**从头截断**（保留结尾）。
    摘要的写法是"越早的事在前面、越近的在后面"（它是增量合并出来的），
    所以留着尾部 = 留着离当前最近的上下文，那才是接下来最可能用到的部分。
    """
    text = " ".join((summary or "").split())
    if not text or max_chars <= 0:
        return ""
    if len(text) > max_chars:
        logger.info("对话摘要超限，按尾部截断：%s → %s 字", len(text), max_chars)
        text = "…" + text[-max_chars:]
    return SUMMARY_HEADER + "\n" + text


def build_system_prompt(memory_section: str = "", summary_section: str = "") -> str:
    """完整系统提示词 = 规则 +（可选）对话摘要 +（可选）长期记忆 —— **全项目唯一出口**。

    为什么不让调用方自己拼：纯 RAG 与 Agent 两条路都要用它，
    两处各拼一次必然改一处忘一处（`build_prompt_and_sources` 已经踩过这个坑）。
    两个可选段都没有时**原样返回 `SYSTEM_PROMPT`**，不打任何补丁 ——
    关掉开关就必须与从前逐字一致。

    顺序（摘要 → 记忆）是刻意的：两者都是"背景"，但记忆是**跨会话的稳定事实**、
    更贴近当前问题，所以放在最后。改顺序要重新验证一次"背景不会盖过资料"
    （记忆那次实测见 README 的「实测数据」）。
    """
    parts = [SYSTEM_PROMPT]
    if summary_section:
        parts.append(summary_section)
    if memory_section:
        parts.append(memory_section)
    if len(parts) == 1:
        return SYSTEM_PROMPT
    return "\n".join(parts) + "\n"
