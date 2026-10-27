"""直播运营 Agent 固定评测集的生成与评分。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

from app.agent.tools import _risk_action
from app.rag import llm
from app.services.live_agent_service import SYSTEM_PROMPT


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def structured_actions(case: dict) -> list[dict]:
    if case.get("special_action"):
        return [case["special_action"]]
    return [
        _risk_action(SimpleNamespace(**risk))
        for risk in case.get("risks", [])
    ]


def generate_prediction(case: dict, *, call_llm: bool) -> dict:
    actions = structured_actions(case)
    if not call_llm:
        return {"answer": None, "actions": actions, "usage": None, "elapsed_ms": 0}
    # 与线上 live_agent_service 保持一致：无巡检时直接返回固定提示，不花一次模型调用。
    if case.get("inspection") is None:
        return {
            "answer": "本场还没有巡检记录，当前无法判断是否可以开播。请先执行平台采集和直播前巡检。",
            "actions": actions,
            "usage": None,
            "elapsed_ms": 0,
        }

    payload = {
        "question": case["question"],
        "business_context": {
            "session": {"id": case["id"], "title": case["name"]},
            "inspection": case.get("inspection"),
            "risks": case.get("risks", []),
            "recommended_actions": actions,
            "approvals": [],
            "rpa_tasks": [],
        },
        "knowledge_references": case.get("knowledge_references", []),
    }
    started = time.perf_counter()
    out = llm.chat(
        SYSTEM_PROMPT,
        "请根据下面的 JSON 证据回答。引用知识资料时使用 [1]、[2] 编号；"
        "结构化巡检事实不需要伪装成知识库引用。\n\n"
        + json.dumps(payload, ensure_ascii=False),
        max_tokens=500,
        temperature=0.0,
    )
    return {
        "answer": out["answer"],
        "actions": actions,
        "usage": out.get("usage"),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _explanation_score(answer: str | None, fact_groups: list[list[str]]) -> float | None:
    if answer is None:
        return None
    if not fact_groups:
        return 1.0
    matched = sum(any(term in answer for term in alternatives) for alternatives in fact_groups)
    return matched / len(fact_groups)


def _citation_score(answer: str | None, source_count: int, citation_required: bool) -> float | None:
    if answer is None:
        return None
    cited = [int(value) for value in re.findall(r"\[(\d+)]", answer)]
    valid = sum(1 <= value <= source_count for value in cited)
    invalid = len(cited) - valid
    missed = int(citation_required and valid == 0)
    denominator = valid + invalid + missed
    return 1.0 if denominator == 0 else valid / denominator


def score_case(case: dict, prediction: dict) -> dict:
    expected = case["expected"]
    actions = prediction.get("actions") or []
    actual_by_risk = {row.get("risk_id"): row for row in actions}
    expected_by_risk = {row["risk_id"]: row for row in expected["actions"]}
    correct = 0
    boundary_correct = 0
    target_correct = 0
    for risk_id, gold in expected_by_risk.items():
        actual = actual_by_risk.get(risk_id) or {}
        correct += actual.get("action_type") == gold["action_type"]
        boundary_correct += actual.get("requires_approval") == gold["requires_approval"]
        target_correct += actual.get("expected_value") == gold.get("expected_value")
    count = max(1, len(expected_by_risk))
    extras = len(set(actual_by_risk) - set(expected_by_risk))
    return {
        "case_id": case["id"],
        "name": case["name"],
        "risk_count": len(case.get("risks", [])),
        "action_accuracy": correct / count,
        "approval_boundary_accuracy": boundary_correct / count,
        "target_value_accuracy": target_correct / count,
        "wrong_action_rate": extras / max(1, len(actions)),
        "risk_traceability_rate": (
            sum(row.get("risk_id") is not None for row in actions) / max(1, len(actions))
        ),
        "explanation_completeness": _explanation_score(
            prediction.get("answer"), expected.get("answer_fact_groups", [])
        ),
        "citation_accuracy": _citation_score(
            prediction.get("answer"),
            len(case.get("knowledge_references", [])),
            expected.get("citation_required", False),
        ),
        "elapsed_ms": prediction.get("elapsed_ms", 0),
        "tokens": (prediction.get("usage") or {}).get("total_tokens"),
        "answer": prediction.get("answer"),
    }


def aggregate(rows: list[dict]) -> dict:
    names = (
        "action_accuracy",
        "approval_boundary_accuracy",
        "target_value_accuracy",
        "wrong_action_rate",
        "risk_traceability_rate",
        "explanation_completeness",
        "citation_accuracy",
    )
    result = {}
    for name in names:
        values = [row[name] for row in rows if row[name] is not None]
        result[name] = sum(values) / len(values) if values else None
    measured = [
        result[name]
        for name in (
            "action_accuracy", "approval_boundary_accuracy", "target_value_accuracy",
            "risk_traceability_rate", "explanation_completeness", "citation_accuracy",
        )
        if result[name] is not None
    ]
    result["overall_score"] = sum(measured) / len(measured) if measured else None
    result["total_tokens"] = sum(row["tokens"] or 0 for row in rows)
    result["total_elapsed_ms"] = sum(row["elapsed_ms"] for row in rows)
    result["case_count"] = len(rows)
    return result


def quality_gate(summary: dict, *, live_llm: bool) -> list[str]:
    """返回未达标项；空列表表示本次评测可以进入下一阶段。"""
    failures = []
    for key, label in (
        ("action_accuracy", "结构化动作正确率"),
        ("approval_boundary_accuracy", "审批边界正确率"),
        ("target_value_accuracy", "目标值正确率"),
        ("risk_traceability_rate", "风险可追踪率"),
    ):
        if summary.get(key) != 1.0:
            failures.append(f"{label}必须为 100%")
    if summary.get("wrong_action_rate") != 0.0:
        failures.append("错误动作率必须为 0%")
    if live_llm:
        if (summary.get("explanation_completeness") or 0) < 0.85:
            failures.append("风险解释完整率必须不低于 85%")
        if (summary.get("citation_accuracy") or 0) < 0.90:
            failures.append("知识引用准确率必须不低于 90%")
    return failures
