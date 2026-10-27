"""Agent 的状态定义。

LangGraph 里的"状态"就是在节点之间传递、由各节点**增量更新**的字典。
字段怎么切分直接决定这张图能不能表达清楚，所以每个字段都标注了它的来源。

⚠️ 实测（langgraph 1.2.12）：状态字段默认是**整体覆盖**语义 ——
节点返回啥就覆盖啥，框架不会自动合并。所以 `retrieved` / `steps` 这类要累积的列表，
必须在节点里手动写 `旧 + 新`（除非用 `Annotated[list, operator.add]` 声明 reducer）。
这里选手动拼：多一行代码，但合并逻辑看得见、格式可控。
"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # ---------- 入参 ----------
    query: str
    kb_ids: list[int] | None
    top_k: int
    # 会话历史（Phase 9）：[("user", 问题), ("assistant", 答案), …]，由 conversation_service 备好。
    # ⚠️ 只喂给**生成**节点 —— 判定节点（analyze/evaluate）刻意不看历史：
    # 它们判的是"这句话本身要不要查库""这批资料够不够答这个问题"，
    # 混进上文只会让判定变模糊，还多花 token。
    history: list
    # 长对话摘要（Phase 12）：较早轮次压成的一段背景，由 conversation_service 备好
    # （与 history 一样由调用方准备）。**同样只喂生成节点** ——
    # 判定节点判的是"这句话要不要查库""这批资料够不够"，掺进旧谈话摘要只会让判定变模糊。
    summary: str

    # ---------- analyze 节点（LLM 决策）----------
    # 这个问题要不要查知识库？"你好"、"你能做什么"这类不该白跑一次检索
    need_retrieval: bool
    # 怎么取得答案 —— analyze 输出的路由指令：
    #   "search"：语义检索（默认，拿不准都归它）
    #   "docs"  ：用户点名了某几份文档，走 fetch_docs 取全文再生成（总结 / 对比）
    #   "list"  ：问"有哪些文档"，走 list_docs 直接产出清单（不经模型）
    #   "general"：通用知识，允许模型直接回答但明确标注未使用企业资料
    #   "tool_time"：当前日期时间，走确定性工具
    #   "skip"  ：寒暄 / 系统性问题，不取数
    # need_retrieval 保留为它的粗粒度投影（mode != "skip"），旧调用方与 generate 的判断不动。
    mode: str
    # mode="docs" 时，模型从问题里点出的文档关键词（最多 3 个，见 graph._parse_analyze）
    doc_names: list[str]

    # ---------- retrieve / evaluate 的累积 ----------
    retrieval_query: str      # 本轮实际使用的检索词（evaluate 可能改写它）
    retrieved: list[dict]     # 累计检索结果（按 chunk_id 去重）
    iterations: int           # 已经**检索**了几轮（文档任务不检索，是 0）
    # 本次是否**取过数**（检索 或 取指定文档）。与 iterations 拆开是有原因的：
    # 文档任务取到了全文但没有"检索轮数"可言（iterations=0），而 searched_kb_ids
    # 的判定要按"取没取过数"—— 早先两者共用一个字段，导致界面把文档任务显示成
    # "检索轮数：1"（它一次检索都没做），或者反过来把范围标错。
    fetched: bool
    added_last: int           # 最近一轮**新增**了几条（去重后）—— 用来判断"换词有没有用"
    sufficient: bool          # 现有资料够不够回答
    # 检索层算出的**真实范围**（已与用户权限求交集），由 retrieve 节点写入。
    # 它是检索结果的一部分，不是入参 kb_ids 的回显 —— 后者没求过交集。
    searched_kb_ids: list[int] | None
    # 文档清单（LIST）**列出过**的库 id 集合，由 list_docs 节点写入 ——
    # 清单正文带【库名】+《文件名》而没有 sources，撤权后的回放脱敏靠它判定。
    # （不能用 searched_kb_ids：它对管理员是 None，表达不了"列了哪些库"。）
    catalog_kb_ids: list[int]
    # 取数过程的"诚实说明"：未匹配到整篇的名字 / 因预算被裁的名字 / 被截断的文档名。
    # 由 fetch_docs 写入、generate 拼进提示词 —— 让答案主动交代"缺了什么"，
    # 而不是看起来完整（缺一半的"对比"比答不出来更坏）。
    doc_notes: list[str]

    # ---------- 最终产物 ----------
    answer: str
    hit: bool
    sources: list[dict]
    answer_mode: str          # grounded / no_evidence / smalltalk / general / tool 等
    steps: list[dict]         # 轨迹：[{"node": "retrieve", "ms": 820, "iteration": 1}]
    usage: dict               # token 累计 {input_tokens, output_tokens, total_tokens}

    # ---------- 异常兜底 ----------
    error: str | None
