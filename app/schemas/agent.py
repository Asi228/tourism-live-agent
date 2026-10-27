"""Agent 问答的请求 / 响应模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import ChatUsage, SourceItem


class AgentChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="用户问题")
    kb_ids: list[int] | None = Field(
        default=None,
        description="限定在这些知识库内检索；不传则用当前用户所有可访问的库。"
                    "与 /api/chat、/api/search 一样会与用户实际权限取交集。",
    )
    top_k: int | None = Field(default=None, ge=1, le=20, description="每次检索取几条")
    conversation_id: int | None = Field(
        default=None,
        description="接着哪个会话继续问（Phase 9）。不传＝开启一个新会话。"
                    "与 /api/chat 共用同一套会话表，所以两边可以轮流问同一段对话。",
    )


class AgentStep(BaseModel):
    """一步执行轨迹。

    它存在的意义：Agent 和普通 RAG 的唯一区别就是"它自己决定怎么走"，
    而这个过程默认不可见。把每一步都返回出来（前端能画成时间线），
    "为什么这次绕了两圈"就不再需要靠猜。
    """

    model_config = ConfigDict(extra="allow")

    node: str = Field(description="节点名：analyze / retrieve / evaluate / generate")
    ms: int = Field(description="该步耗时（毫秒）")
    # 其余字段随节点而异（decision / iteration / query / hits / mode / adopted …），
    # 用 extra="allow" 放开，避免为每个节点各造一个子模型。


class AgentChatResponse(BaseModel):
    query: str
    answer: str
    answer_mode: Literal["grounded", "no_evidence", "smalltalk", "faq", "catalog"] = Field(
        default="no_evidence",
        description="本次答案的**性质**，比 hit 更细："
                    "grounded=依据知识库资料作答；"
                    "no_evidence=检索过但没有可用资料（不调模型）；"
                    "smalltalk=判定不需要查库，寒暄式回应；"
                    "faq=命中知识库常见问题，直接返回标准答案（没检索、没调模型）；"
                    "catalog=文档清单（直接列可见文档，答案来自系统元数据、未经模型生成）。"
                    "hit=false 时靠它区分「没有依据」和「本就不该有依据」。"
                    "⚠️ 取值改动要同步 app/agent/graph.py 里的 ANSWER_* 常量、"
                    "faq_service.ANSWER_MODE_FAQ 与前端 utils/answerMode.js。",
    )
    hit: bool = Field(description="是否拿到了知识库资料作为依据")
    sources: list[SourceItem] = Field(default_factory=list)
    searched_kb_ids: list[int] | None = Field(
        default=None,
        description="本次**实际参与检索**的知识库 id（由检索层算出、已与用户权限求交集）。"
                    "[] 表示判定为不需要检索、本次没查任何库；"
                    "None 表示系统管理员不受限（即没有范围限制）。",
    )
    iterations: int = Field(description="实际检索轮数；0 表示判定为「不需要查知识库」")
    steps: list[AgentStep] = Field(default_factory=list, description="执行轨迹，按发生顺序")
    usage: ChatUsage | None = Field(default=None, description="本次**全部** LLM 调用的 token 合计")
    elapsed_ms: int = Field(description="端到端耗时（毫秒）")
    conversation_id: int | None = Field(
        default=None,
        description="本次问答所属的会话 id（Phase 9）。不传 conversation_id 时由后端隐式创建，"
                    "后续轮次带上它就能接着上文问。",
    )


class LiveSessionAgentRequest(BaseModel):
    question: str = Field(
        default="为什么本场直播现在不能开播？请按优先级给出处理建议。",
        min_length=1,
        max_length=1000,
    )
    kb_ids: list[int] | None = Field(
        default=None, description="可选：限定用于补充规则依据的运营知识库"
    )


class LiveSessionAgentResponse(BaseModel):
    session_id: int
    inspection_id: int | None
    answer: str
    conclusion: str | None
    score: int | None
    risks: list[dict] = Field(default_factory=list)
    recommended_actions: list[dict] = Field(default_factory=list)
    approvals: list[dict] = Field(default_factory=list)
    rpa_tasks: list[dict] = Field(default_factory=list)
    sources: list[SourceItem] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    usage: ChatUsage | None = None
    elapsed_ms: int


class ApprovalDraftRequest(BaseModel):
    risk_id: int = Field(ge=1, description="最新巡检中的失败结果 ID")


class ApprovalDraftResponse(BaseModel):
    session_id: int
    inspection_id: int
    source_inspection_result_id: int
    task_type: Literal["price_fix", "delist"]
    sku_id: int
    sku_code: str
    expected_value: dict
    reason: str
    evidence: dict
    can_submit: bool
    existing_approval_id: int | None = None
