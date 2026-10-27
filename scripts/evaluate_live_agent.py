"""运行直播运营 Agent 固定评测集并生成 Markdown/JSON 报告。"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.getcwd())

from app.evaluation.live_agent import (
    aggregate, generate_prediction, load_cases, quality_gate, score_case,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "live_agent_cases.json"


def _percent(value):
    return "未测" if value is None else f"{value * 100:.1f}%"


def write_report(path: Path, rows: list[dict], summary: dict, *, live_llm: bool) -> None:
    mode = "真实 LLM 生成 + 结构化动作" if live_llm else "结构化动作（未调用 LLM）"
    lines = [
        f"# 直播运营 Agent 固定评测（{date.today().isoformat()}）", "",
        f"- 数据集：`evals/live_agent_cases.json`，共 {summary['case_count']} 个业务场景。",
        f"- 模式：{mode}。", "",
        "## 汇总指标", "",
        "| 指标 | 结果 |", "|---|---:|",
        f"| 结构化动作正确率 | {_percent(summary['action_accuracy'])} |",
        f"| 审批边界正确率 | {_percent(summary['approval_boundary_accuracy'])} |",
        f"| 目标值正确率 | {_percent(summary['target_value_accuracy'])} |",
        f"| 错误动作率 | {_percent(summary['wrong_action_rate'])} |",
        f"| 风险可追踪率 | {_percent(summary['risk_traceability_rate'])} |",
        f"| 风险解释完整率 | {_percent(summary['explanation_completeness'])} |",
        f"| 知识引用准确率 | {_percent(summary['citation_accuracy'])} |",
        f"| 综合得分 | {_percent(summary['overall_score'])} |",
        f"| 总耗时 | {summary['total_elapsed_ms']} ms |",
        f"| 总 token | {summary['total_tokens']} |", "",
        "质量门槛：动作、审批边界、目标值、风险追踪必须为 100%，错误动作率必须为 0%；"
        "真实模型模式下解释完整率不低于 85%，引用准确率不低于 90%。", "",
        "## 分场景结果", "",
        "| 场景 | 动作 | 审批边界 | 解释完整 | 引用 | 耗时 |", "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {_percent(row['action_accuracy'])} | "
            f"{_percent(row['approval_boundary_accuracy'])} | "
            f"{_percent(row['explanation_completeness'])} | "
            f"{_percent(row['citation_accuracy'])} | {row['elapsed_ms']} ms |"
        )
    if not live_llm:
        lines += ["", "> 本次未调用 LLM，因此风险解释完整率和知识引用准确率标记为“未测”。运行时增加 `--live-llm` 可补齐生成质量指标。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="直播运营 Agent 固定评测")
    parser.add_argument("--live-llm", action="store_true", help="真实调用当前配置的大模型")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "evaluations")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    predictions = []
    rows = []
    for case in cases:
        prediction = generate_prediction(case, call_llm=args.live_llm)
        predictions.append({"case_id": case["id"], **prediction})
        row = score_case(case, prediction)
        rows.append(row)
        print(
            f"[{case['id']}] 动作={_percent(row['action_accuracy'])} "
            f"审批={_percent(row['approval_boundary_accuracy'])} "
            f"解释={_percent(row['explanation_completeness'])} 引用={_percent(row['citation_accuracy'])}"
        )
    summary = aggregate(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mode_suffix = "live" if args.live_llm else "offline"
    stem = f"live_agent_eval_{date.today().isoformat()}_{mode_suffix}"
    report_path = args.output_dir / f"{stem}.md"
    json_path = args.output_dir / f"{stem}.json"
    write_report(report_path, rows, summary, live_llm=args.live_llm)
    json_path.write_text(
        json.dumps({"summary": summary, "results": rows, "predictions": predictions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告：{report_path}")
    print(f"原始结果：{json_path}")
    failures = quality_gate(summary, live_llm=args.live_llm)
    if failures:
        print("质量门槛未通过：" + "；".join(failures))
        raise SystemExit(1)
    print("质量门槛：通过")


if __name__ == "__main__":
    main()
