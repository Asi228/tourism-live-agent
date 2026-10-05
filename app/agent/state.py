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

    # ---------- analyze 节点（LLM 决策）----------
    # 这个问题要不要查知识库？"你好"、"你能做什么"这类不该白跑一次检索
    need_retrieval: bool

    # ---------- retrieve / evaluate 的累积 ----------
    retrieval_query: str      # 本轮实际使用的检索词（evaluate 可能改写它）
    retrieved: list[dict]     # 累计检索结果（按 chunk_id 去重）
    iterations: int           # 已经检索了几轮
    added_last: int           # 最近一轮**新增**了几条（去重后）—— 用来判断"换词有没有用"
    sufficient: bool          # 现有资料够不够回答
    # 检索层算出的**真实范围**（已与用户权限求交集），由 retrieve 节点写入。
    # 它是检索结果的一部分，不是入参 kb_ids 的回显 —— 后者没求过交集。
    searched_kb_ids: list[int] | None

    # ---------- 最终产物 ----------
    answer: str
    hit: bool
    sources: list[dict]
    answer_mode: str          # grounded / no_evidence / smalltalk（见 graph.py 的常量）
    steps: list[dict]         # 轨迹：[{"node": "retrieve", "ms": 820, "iteration": 1}]
    usage: dict               # token 累计 {input_tokens, output_tokens, total_tokens}

    # ---------- 异常兜底 ----------
    error: str | None
