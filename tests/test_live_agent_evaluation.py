"""直播运营 Agent 固定评测器自身的回归测试。"""

from pathlib import Path

from app.evaluation.live_agent import (
    aggregate, generate_prediction, load_cases, quality_gate, score_case,
)


CASES = Path(__file__).resolve().parents[1] / "evals" / "live_agent_cases.json"


def test_fixed_dataset_covers_business_risks_and_structured_actions():
    cases = load_cases(CASES)
    assert len(cases) == 7
    assert {case["id"] for case in cases} == {
        "no-inspection", "inventory-shortage", "price-mismatch",
        "promotion-inactive", "sku-inactive", "missing-documents", "multiple-risks",
    }
    rows = [
        score_case(case, generate_prediction(case, call_llm=False))
        for case in cases
    ]
    summary = aggregate(rows)
    assert summary["action_accuracy"] == 1.0
    assert summary["approval_boundary_accuracy"] == 1.0
    assert summary["target_value_accuracy"] == 1.0
    assert summary["wrong_action_rate"] == 0.0
    assert summary["risk_traceability_rate"] == 1.0
    assert summary["explanation_completeness"] is None
    assert quality_gate(summary, live_llm=False) == []


def test_scorer_detects_wrong_action_missing_fact_and_bad_citation():
    case = next(row for row in load_cases(CASES) if row["id"] == "price-mismatch")
    result = score_case(case, {
        "answer": "价格有问题，参考不存在的资料 [2]。",
        "actions": [{
            "risk_id": 1201,
            "action_type": "refresh_or_restock",
            "requires_approval": False,
            "expected_value": {"live_price": "88.00"},
        }],
        "usage": None,
        "elapsed_ms": 1,
    })
    assert result["action_accuracy"] == 0.0
    assert result["approval_boundary_accuracy"] == 0.0
    assert result["target_value_accuracy"] == 0.0
    assert result["explanation_completeness"] < 1.0
    assert result["citation_accuracy"] == 0.0
    failed = quality_gate(aggregate([result]), live_llm=True)
    assert "结构化动作正确率必须为 100%" in failed
    assert "知识引用准确率必须不低于 90%" in failed


def test_no_inspection_live_mode_uses_online_fixed_answer(monkeypatch):
    from app.evaluation import live_agent

    case = next(row for row in load_cases(CASES) if row["id"] == "no-inspection")
    monkeypatch.setattr(
        live_agent.llm,
        "chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("无巡检场景不应调用模型")
        ),
    )
    prediction = generate_prediction(case, call_llm=True)
    assert "还没有巡检记录" in prediction["answer"]
    assert prediction["usage"] is None
    assert prediction["elapsed_ms"] == 0
