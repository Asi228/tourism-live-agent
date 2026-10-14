"""Agent 状态机装配（Phase 8）。

**为什么这里需要状态机，而不是几行顺序调用**：

    "要怎么取数"（检索 / 取指定文档 / 不取） → 取决于问题本身；
    "还要不要再检索一次" → 取决于**上一轮检索回来的资料够不够**。

第二件事是顺序调用表达不了的：它需要「条件分支 + 回到上一步的循环 + 跨轮累积的状态」。
LangGraph 的 StateGraph 正好把这三样变成显式的东西 ——
图长什么样，Agent 的决策逻辑就长什么样，可以被画出来、被测试、被讲清楚。

     analyze ──SKIP──→ generate → END
        │
        ├─ SEARCH → retrieve → evaluate ──够──→ generate → END
        │                         │
        │                      不够且没到轮数上限
        │                         └──────→ 回到 retrieve
        │
        └─ DOCS → fetch_docs → generate → END
                 （取点名文档的全文：总结 / 对比；
                   一份都没匹配到则**回落成检索**，误判的代价约等于零）

**三道成本闸门**（Agent 比纯 RAG 多调几次模型，必须掐住）：
  1. `AGENT_MAX_ITERATIONS`：最多检索几轮（默认 2），到顶强制生成
  2. 判定节点输出极短（`max_tokens` 8~32）：只要一个指令，不给它长篇大论的机会；
     文档任务另有 `MAX_DOC_FETCH=3`（最多取三份文档）与"每份分预算"
  3. 调用方还会传 `recursion_limit`（LangGraph 自带的硬闸门，超限抛 GraphRecursionError）
"""

import logging
import re
import time

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent import tools
from app.agent.state import AgentState
from app.config import settings
from app.models.user import User
from app.rag import llm, prompt
from app.services import memory_service

logger = logging.getLogger(__name__)

# 判定节点的输出上限。analyze 要输出三种指令之一（SEARCH / DOCS: a | b / SKIP），
# 比"一个词"长一点（得容下文档名），但仍然压得很紧 —— 没有"解释一堆"的空间，
# 输出短既是省钱，也让解析不脆；evaluate 保持 8 不动（它是真的只要一个词）。
DECIDE_MAX_TOKENS = 8
ANALYZE_MAX_TOKENS = 32

# 文档任务一次最多取几份文档（成本闸门）。"对比两份"是典型场景，给到 3 留一点余量。
MAX_DOC_FETCH = 3

ANALYZE_SYSTEM = """你是企业知识库问答系统的调度器。判断用户这句话要怎么处理，只输出对应格式之一：

· 问具体的业务问题（制度、流程、规定）→ 输出：SEARCH
· 要针对**某几份具名文档**做操作（总结 / 对比 / 提取要点），句子里提到了文档关键词
  → 输出：DOCS: 关键词1 | 关键词2
  （关键词给**最有区分度的短词**，如"考勤"、"报销"；别带"制度""文档"这类通用后缀）
· 问候、自我介绍、"你能做什么"这类系统性问题 → 输出：SKIP

只输出上述格式之一，不要解释。"""

EVALUATE_SYSTEM = """你是检索质量判断器。给你一个问题和一批已检索到的资料，判断**仅凭这些资料能否回答这个问题**。

· 能回答 → 只输出：SUFFICIENT
· 不能回答（资料与问题无关，或缺少关键信息）→ 输出一行：RETRY: <换一个更可能检索到答案的关键词>

只输出上面两种格式之一，不要解释。"""

# "不需要查库"时的直答口径：允许寒暄，但**绝不允许编造公司具体信息**
DIRECT_SYSTEM = """你是企业知识库问答助手。用户这句话不需要查询公司文档。

请简短友好地回应，并引导用户提出与公司制度、流程、规定相关的具体问题。
严禁编造任何公司相关的具体信息（天数、金额、人名、流程细节等）。"""

# 业务疑问词。命中就**不走模型判定**，直接按"需要检索"处理。
#
# 为什么需要这道规则：analyze 判 SKIP 时，走的是**全系统唯一允许模型在毫无资料的情况下
# 自由发挥**的路径（DIRECT_SYSTEM + temperature=0.5），而且那条路不经过 evaluate ——
# 也就是说模型一旦把业务问题误判成寒暄，**没有任何机制能纠正它**。
# 代价不对称：多检索一次只是多花几分钱，而"年假几天"被当成寒暄答掉是产品级事故。
BUSINESS_HINTS = (
    "多少", "几天", "几日", "几号", "多久", "几次", "什么时候",
    "流程", "规定", "制度", "政策", "标准", "额度", "比例", "条件", "要求",
    "报销", "申请", "审批", "批准", "考核", "考勤", "打卡", "加班", "调休",
    "年假", "请假", "病假", "婚假", "产假", "事假", "假期",
    "薪酬", "工资", "社保", "公积金", "补贴", "奖金", "福利",
    "入职", "离职", "转正", "试用期", "合同",
    "怎么", "如何", "能不能", "可以吗", "是否", "允许", "禁止", "处罚", "违规",
)


def _business_hint(query: str) -> str | None:
    """命中业务疑问词就返回它（写进轨迹便于回溯），否则 None。"""
    q = query or ""
    for word in BUSINESS_HINTS:
        if word in q:
            return word
    return None


# 文档任务特征词：出现它们时**不**走业务词的规则短路 —— 用户可能是在点名某几份文档。
# 例："对比考勤和报销制度"里"考勤"会命中业务词，但真正的意图是 DOCS（取全文再对比），
# 短路成 SEARCH 会漏掉这个能力。
#
# 代价是对称地算过的：这类句子会多花一次 analyze 的模型调用；而**多判**成 DOCS 的
# 代价 ≈ 0（文档名匹配不上会回落检索，见 fetch_docs）——漏判则功能静默失效。
# 所以宁可多问模型一次。
_DOC_TASK_WORDS = ("对比", "比较", "总结", "归纳", "提取", "梳理", "整理", "两份")


def _doc_task_hint(query: str) -> str | None:
    q = query or ""
    for word in _DOC_TASK_WORDS:
        if word in q:
            return word
    return None


# generate 节点的三种产出模式。它回答的是"这次答案**性质**是什么"：
# 只看 hit 分不清「检索了但没资料」与「压根没检索（寒暄直答）」，
# 而这两者的可信度完全不同 —— 前者是"没有依据"，后者是"本就不该有依据"。
ANSWER_GROUNDED = "grounded"        # 依据知识库资料作答
ANSWER_NO_EVIDENCE = "no_evidence"  # 检索过但没资料（含"有命中但一条都拼不进上下文"）
ANSWER_SMALLTALK = "smalltalk"      # 判定不需要查库，寒暄式回应（唯一允许无资料发挥的口子）


def _step(node: str, started: float, **detail) -> dict:
    """一条轨迹：节点名 + 耗时 + 业务细节。它是"Agent 到底怎么走的"唯一证据。"""
    return {"node": node, "ms": int((time.monotonic() - started) * 1000), **detail}


def _parse_analyze(text: str) -> tuple[str, list[str]]:
    """解析调度器的输出 → `(mode, doc_names)`，mode ∈ {"search", "docs", "skip"}。

    拿不准时一律默认 "search" —— 与旧版 NEED/SKIP 同一个降级方向：
    宁可多花一次检索的钱，也不能让知识库问答凭空作答（或反过来漏掉取数）。
    """
    t = (text or "").strip()

    m = re.search(r"DOCS?\s*[:：]\s*(.+)", t, re.I)
    if m:
        # 分隔符兼容中英文：| ｜ , ， 、 /
        names = [x.strip() for x in re.split(r"[|｜,，、/]", m.group(1)) if x.strip()]
        names = [n[:40] for n in names[:MAX_DOC_FETCH]]  # 最多 3 份（成本闸门）
        # 说了 DOCS 却没给出名字（模型跑偏）→ 当检索处理，别空转一轮
        return ("docs", names) if names else ("search", [])

    u = t.upper()
    if "SKIP" in u or u.startswith("NO"):
        return "skip", []
    return "search", []


def _parse_evaluate(text: str) -> tuple[bool, str | None]:
    """解析判断结果，返回 `(资料是否足够, 新的检索词)`。

    默认是"足够"：判断器没明确说 RETRY 就停下来，
    避免因为解析不出内容而反复检索（那才是真的烧钱）。
    """
    t = (text or "").strip()
    m = re.search(r"RETRY\s*[:：]\s*(.+)", t, re.I)
    if m:
        new_query = m.group(1).strip().splitlines()[0][:200]
        return False, (new_query or None)
    return True, None


def _merge(old: list[dict], new: list[dict]) -> list[dict]:
    """按 chunk_id 去重地合并两批检索结果（改写检索词后很可能召回重复内容）。"""
    seen = {d.get("chunk_id") for d in old}
    merged = list(old)
    for item in new:
        if item.get("chunk_id") not in seen:
            merged.append(item)
            seen.add(item.get("chunk_id"))
    return merged


def _judge_input(query: str, docs: list[dict], limit: int = 200) -> str:
    """把资料压成给判断器看的清单（正文截断，省 token）。"""
    if not docs:
        # 一条都没检索到也要问一次 —— 目的不是"判断"，而是**要一个新关键词**
        return "问题：%s\n\n已检索到的资料：（空，一条都没有检索到）" % query
    lines = []
    for i, d in enumerate(docs[:5], 1):
        body = " ".join((d.get("content") or "").split())[:limit]
        lines.append("[%d] %s：%s" % (i, d.get("document_name") or "未知文档", body))
    return "问题：%s\n\n已检索到的资料：\n%s" % (query, "\n".join(lines))


def build_graph(db: Session, user: User, kb_ids: list[int] | None = None, top_k: int | None = None):
    """为一次请求装配状态机。

    每次请求都重新 build：图结构是纯 Python 对象，编译在毫秒级；
    好处是节点能用闭包直接拿到 `db` / `user` —— 不必把 Session 塞进状态或 config
    （Session 不可序列化，塞进状态会让状态变重，将来也没法跨进程/持久化）。
    """

    def analyze(state: AgentState) -> dict:
        started = time.monotonic()

        # 规则先手：命中业务词就不问模型了 —— 省一次调用，也堵住 SKIP 误判。
        # ⚠️ 例外：句子里出现"对比 / 总结"这类文档任务词时不短路 —— 用户可能是在
        # 点名某几份文档，要交给模型区分 SEARCH 与 DOCS（见 _DOC_TASK_WORDS 的代价分析）。
        hint = _business_hint(state["query"])
        if hint and not _doc_task_hint(state["query"]):
            return {
                "need_retrieval": True,
                "mode": "search",
                "steps": state.get("steps", []) + [
                    _step("analyze", started, decision="SEARCH", rule="business_word:%s" % hint)
                ],
            }

        try:
            out = llm.chat(
                ANALYZE_SYSTEM, state["query"], max_tokens=ANALYZE_MAX_TOKENS, temperature=0
            )
            mode, names = _parse_analyze(out["answer"])
            decision, tokens = out["answer"].strip()[:40], out["usage"] or {}
        except Exception as exc:  # noqa: BLE001
            # 判定失败不致命：当作"需要检索"继续走下去（失败要降级，不能整个问答挂掉）
            logger.warning("Agent 判定节点失败，按「需要检索」继续：%s", str(exc)[:200])
            mode, names, decision, tokens = "search", [], "(判定失败→默认检索)", {}
        return {
            "need_retrieval": mode != "skip",
            "mode": mode,
            "doc_names": names,
            "steps": state.get("steps", []) + [_step("analyze", started, decision=decision)],
            "usage": _accumulate(state.get("usage"), tokens),
        }

    def retrieve(state: AgentState) -> dict:
        started = time.monotonic()
        keyword = state.get("retrieval_query") or state["query"]
        before = len(state.get("retrieved", []))
        got = tools.search_knowledge(db, user, keyword, kb_ids=kb_ids, top_k=top_k)
        merged = _merge(state.get("retrieved", []), got.get("results", []))
        iteration = state.get("iterations", 0) + 1
        return {
            "retrieved": merged,
            "iterations": iteration,
            # 真实检索范围由检索那一层算出来（已与用户权限求过交集），必须原样带出去 ——
            # 不能回显请求里的 kb_ids：那个没求过交集，而本字段的 None 语义是「管理员不受限」，
            # 回显会把普通员工标成"扫全库"。
            "searched_kb_ids": got.get("searched_kb_ids"),
            # 这一轮**净增**了几条。它是"换个词到底有没有用"的唯一客观依据 ——
            # 实测：问知识库里没有的内容时，改写过的关键词召回的还是同样那几条，
            # 净增 0，却又白跑了一轮判断（840 tokens vs 纯 RAG 的 303）。
            "added_last": len(merged) - before,
            "retrieval_query": keyword,
            "steps": state.get("steps", []) + [
                _step("retrieve", started, iteration=iteration, query=keyword,
                      hits=len(got.get("results", [])), added=len(merged) - before,
                      total=len(merged))
            ],
        }

    def fetch_docs(state: AgentState) -> dict:
        """取「用户点名的文档」全文（文档任务：总结 / 对比）。

        取来的块与检索结果**同构**（document_name / content / score…），
        下游的拼上下文、生成引用全部复用同一条路 —— 它在图里只是"另一种取数"。

        两条刻意的设计：
          1. **匹配失败 → 回落普通检索**（而不是直接报"没找到"）。用户可能把名字
             说得不一样（"考勤规定" vs《员工考勤与休假管理制度》），检索是保底；
             LIKE 匹配是本地查询，零成本 —— 所以"模型多判了 DOCS"的代价约等于零，
             这个性质是整个设计敢让模型大胆判的前提。
          2. **每份文档分预算**：总预算按份数平摊。不设的话第一份就能把
             RAG_MAX_CONTEXT_CHARS 吃光，对比任务里第二份被截成空 —— 那是静默的错误答案。
        """
        started = time.monotonic()
        names = list(state.get("doc_names") or [])
        # 每份的预算：总预算里**先留出标题行的余量**（下游 build_context 拼块时
        # 每块会加 "[n] 来源：文档名"），再按份数平摊。
        # ⚠️ 不留余量的话：两份各吃满 RAG_MAX_CONTEXT_CHARS/2，加标题刚好超限 ——
        # 下游会把第二份整块丢掉，"对比"就悄悄变成了"只看第一份"。
        # （这条是 test_docs_budget_split_between_documents 逼出来的）
        overhead = 200
        budget = max(200, (settings.RAG_MAX_CONTEXT_CHARS - overhead) // max(1, len(names)))

        docs: list[dict] = []
        truncated: list[str] = []
        scope = None
        for name in names:
            got = tools.get_document(db, user, name, kb_ids=kb_ids)
            scope = got.get("searched_kb_ids")
            results = got.get("results") or []
            if not results:
                continue
            item = results[0]
            if len(item.get("content") or "") > budget:
                item = {**item, "content": item["content"][:budget]}
                truncated.append(item["document_name"])
            docs.append(item)

        if docs:
            return {
                "retrieved": docs,
                "iterations": 1,
                "searched_kb_ids": scope,
                "added_last": len(docs),
                "retrieval_query": state["query"],
                "steps": state.get("steps", []) + [
                    _step("fetch_docs", started, tool="get_document",
                          asked=" | ".join(names), matched=len(docs),
                          truncated=len(truncated))
                ],
            }

        # 一份都没匹配到 → 回落普通检索（保底路径，轨迹里留痕）
        logger.info("文档任务未匹配到文档（%s），回落检索", " | ".join(names)[:80])
        got = tools.search_knowledge(db, user, state["query"], kb_ids=kb_ids, top_k=top_k)
        results = got.get("results", [])
        return {
            "retrieved": results,
            "iterations": 1,
            "searched_kb_ids": got.get("searched_kb_ids"),
            "added_last": len(results),
            "retrieval_query": state["query"],
            "steps": state.get("steps", []) + [
                _step("fetch_docs", started, tool="get_document",
                      asked=" | ".join(names), matched=0, fallback="search")
            ],
        }

    def evaluate(state: AgentState) -> dict:
        started = time.monotonic()
        docs = state.get("retrieved", [])
        # ⚠️ 即使一条都没检索到，也要走这一次判断。
        # 一度为了省钱在这里直接返回"不够"，结果第二轮只能拿**同一个关键词**再检索一遍 ——
        # 更贵（多一次 embedding + 重排），而且大概率还是空。让判断器给个新词才有意义。
        # （这条是被 tests/test_agent.py::test_retry_query_comes_from_evaluate 逼出来的）
        try:
            out = llm.chat(
                EVALUATE_SYSTEM, _judge_input(state["query"], docs),
                max_tokens=64, temperature=0,
            )
            ok, new_query = _parse_evaluate(out["answer"])
            tokens = out["usage"] or {}
            decision = out["answer"].strip().splitlines()[0][:40] if out["answer"] else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent 评估节点失败，按「资料足够」继续：%s", str(exc)[:200])
            ok, new_query, tokens, decision = True, None, {}, "(评估失败→默认足够)"

        # 说"不够"但没给新关键词 → 再检索一次也是白跑，直接收手
        if not ok and not new_query:
            ok, decision = True, decision or "判定不足但未给新关键词，收工"

        return {
            "sufficient": ok,
            "retrieval_query": new_query or state.get("retrieval_query"),
            "steps": state.get("steps", []) + [_step("evaluate", started, decision=decision)],
            "usage": _accumulate(state.get("usage"), tokens),
        }

    def generate(state: AgentState) -> dict:
        started = time.monotonic()
        docs = state.get("retrieved", [])

        if not docs:
            if state.get("need_retrieval", True):
                # 检索过但没资料 —— 与纯 RAG 完全一致的口径：不调模型，直接给固定话术
                return {
                    "answer": prompt.NO_HIT_ANSWER,
                    "hit": False,
                    "sources": [],
                    "answer_mode": ANSWER_NO_EVIDENCE,
                    "steps": state.get("steps", []) + [_step("generate", started, mode="未命中")],
                }
            # 判定为"不需要查库"：允许寒暄式作答，但提示词已禁止编造公司信息
            out = llm.chat(DIRECT_SYSTEM, state["query"], max_tokens=200, temperature=0.5)
            return {
                "answer": out["answer"],
                "hit": False,
                "sources": [],
                "answer_mode": ANSWER_SMALLTALK,
                "steps": state.get("steps", []) + [_step("generate", started, mode="直答")],
                "usage": _accumulate(state.get("usage"), out["usage"] or {}),
            }

        context, adopted, sources = prompt.build_prompt_and_sources(
            docs, settings.RAG_MAX_CONTEXT_CHARS
        )

        # 检索到了资料，但一条都拼不进上下文（配置过小，或命中的正文全是空白）。
        # 与 rag_service 同一道守卫：拿空资料去调模型 = 幻觉 + 白花钱。
        # 这段守卫原先只在纯 RAG 那一侧有，Agent 侧漏了 —— 抽成公共函数后两边一致。
        if not adopted:
            logger.warning("Agent 上下文为空，跳过生成：命中 %s 条", len(docs))
            return {
                "answer": prompt.NO_HIT_ANSWER,
                "hit": False,
                "sources": [],
                "answer_mode": ANSWER_NO_EVIDENCE,
                "steps": state.get("steps", []) + [_step("generate", started, mode="上下文为空")],
            }

        # history（会话历史，Phase 9）只给生成节点用；判定节点不看它，见 state.py 的说明。
        # 长期记忆（Phase 11）与对话摘要（Phase 12）同源复用 service —— 与纯 RAG 走同一份
        # 拼装逻辑，都在"真要调模型之前"才取，判定/空检索的路径不白查库。
        memory = memory_service.section_for_prompt(db, user)
        out = llm.chat(
            prompt.build_system_prompt(
                memory_section=memory, summary_section=state.get("summary") or ""
            ),
            prompt.build_user_message(state["query"], context),
            history=state.get("history") or [],
        )
        return {
            "answer": out["answer"],
            "hit": True,
            # sources 由 prompt.build_sources 从 adopted 生成 —— 编号与上下文里的 [n] 一一对应
            "sources": sources,
            "answer_mode": ANSWER_GROUNDED,
            "steps": state.get("steps", []) + [
                _step("generate", started, mode="依据资料", adopted=len(adopted))
            ],
            "usage": _accumulate(state.get("usage"), out["usage"] or {}),
        }

    def route_after_analyze(state: AgentState) -> str:
        # mode 缺失时从 need_retrieval 推（兼容旧状态形状）
        mode = state.get("mode") or ("search" if state.get("need_retrieval") else "skip")
        if mode == "skip":
            return "generate"
        if mode == "docs":
            return "fetch_docs"
        return "retrieve"

    def route_after_evaluate(state: AgentState) -> str:
        if state.get("sufficient"):
            return "generate"
        # 已经重试过至少一次、而这一轮**一条新资料都没带来** → 再换关键词大概率还是这一批
        # （语料就那么大），继续下去只是多烧一次检索 + 一次判断。
        # 判 `>= 2` 而不是 `>= 1`：第一次检索为空是**值得**换个词再试一次的，
        # 只有"重试之后仍然毫无新增"才说明这条路走不通。
        #
        # ⚠️ 诚实说明这条规则的实际生效范围：默认 `AGENT_MAX_ITERATIONS=2` 时，
        # 走到这里（iterations>=2）**下面的轮数上限也会返回 generate**，
        # 所以它当前没有可观测的独立效果 —— 实测过，token 数没变化。
        # 它真正省下一轮的前提是把上限调到 >=3（那时 iterations=2 且零新增就会提前收手）。
        # 保留而不删的理由：上限是可配项，而"重试后零新增"这个判断依据本身是对的；
        # 默认值保持不变是**刻意**的 —— 为"让代码看起来不冗余"而多花一轮 LLM 成本不划算。
        # 用例 `test_stops_when_retrieval_adds_nothing` 故意用 MAX=5 才能与"到上限"区分开。
        if state.get("iterations", 0) >= 2 and state.get("added_last", 0) == 0:
            logger.info("Agent 上轮检索无新增资料，停止重试直接生成")
            return "generate"
        if state.get("iterations", 0) >= max(1, settings.AGENT_MAX_ITERATIONS):
            # 轮数到顶：这是防止"边检索边烧钱"的硬闸门，必须先生效再谈质量
            logger.info("Agent 达到检索轮数上限 %s，强制生成", settings.AGENT_MAX_ITERATIONS)
            return "generate"
        return "retrieve"

    graph = StateGraph(AgentState)
    graph.add_node("analyze", analyze)
    graph.add_node("retrieve", retrieve)
    graph.add_node("fetch_docs", fetch_docs)
    graph.add_node("evaluate", evaluate)
    graph.add_node("generate", generate)

    graph.add_edge(START, "analyze")
    graph.add_conditional_edges(
        "analyze",
        route_after_analyze,
        {"retrieve": "retrieve", "fetch_docs": "fetch_docs", "generate": "generate"},
    )
    graph.add_edge("retrieve", "evaluate")
    # 文档任务直连 generate：全文已取到（或已回落检索），没有"够不够"要判、也没有词可改写
    graph.add_edge("fetch_docs", "generate")
    graph.add_conditional_edges(
        "evaluate", route_after_evaluate, {"retrieve": "retrieve", "generate": "generate"}
    )
    graph.add_edge("generate", END)

    return graph.compile()


def _accumulate(total: dict | None, delta: dict) -> dict:
    """把多次 LLM 调用的 token 累加起来（Agent 的用量必须能一次看清）。"""
    out = dict(total or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if delta.get(key):
            out[key] = (out.get(key) or 0) + delta[key]
    return out
