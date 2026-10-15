"""性能基准 —— 把「系统有多快」变成可复现的数字。

用法（会真实调用硅基流动 embedding/rerank 与 DeepSeek LLM，约 3 轮 × 8 问 = 24 次
生成调用，费用几分钱量级）：

    .venv\\Scripts\\python.exe scripts\\benchmark.py [--rounds 3]

前提：MySQL / Milvus / Redis 可达（VM 开机），.env 里 API key 有效。

测什么、为什么测这三段：
  · 检索（向量化 + 双路召回 + RRF + 回库取正文）—— 一次 vector_service.search；
  · 重排 —— reranker.rerank 对检索候选精排；
  · 生成 —— prompt 组装 + llm.chat。
三段加起来就是端到端。全部只调**公开函数**，与线上同一条链路，
所以测出来的数字就是用户等的时间（串行、单用户，无并发）。

产出：docs/benchmark_<日期>.md（表格）+ 终端摘要。报告数字变化时重跑即可，
旧报告留着当历史对照。
"""

import argparse
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, os.getcwd())  # 直接 `python scripts\benchmark.py` 时把仓库根挂上 path

from app.config import settings
from app.db.session import new_session
from app.models.user import User
from app.rag import llm, reranker
from app.rag.prompt import build_prompt_and_sources, build_system_prompt
from app.services import vector_service

# 按当前语料（制度类文档）选的问题：覆盖有依据 / 无依据 / 编号专名三类。
# 换语料时改这里，重跑即可。
QUESTIONS = [
    "年假几天？怎么申请？",
    "请假流程是什么？",
    "试用期多久？",
    "报销需要什么材料？",
    "迟到早退怎么处理？",
    "考勤制度有哪些规定？",
    "ISO27001 认证有什么要求？",
    "量子纠缠的物理原理是什么？",
]


def _stats(samples: list[float]) -> dict:
    """一组耗时的统计量。空列表返回全 None（调用方负责展示）。"""
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "n": len(samples),
        "mean": statistics.fmean(samples),
        "p50": statistics.median(samples),
        "p95": p95,
        "max": max(samples),
    }


def _fmt(ms: float | None) -> str:
    return "-" if ms is None else f"{ms:.0f}"


def run_one_question(db, user, query: str, skip_llm: bool = False) -> dict:
    """跑一遍完整链路，返回各段耗时（毫秒）与当次是否走到了生成。"""
    t: dict[str, float] = {}

    t0 = time.perf_counter()
    out = vector_service.search(
        db, user, query, kb_ids=None, top_k=settings.RAG_TOP_K, rerank=False
    )
    t["search_ms"] = (time.perf_counter() - t0) * 1000

    results = out["results"]
    t0 = time.perf_counter()
    if results and settings.RAG_RERANK_ENABLED:
        pairs = reranker.rerank(
            query, [r["content"] for r in results], top_n=settings.RAG_TOP_K
        )
        # 按 rerank 返回的顺序重排（与 vector_service._apply_rerank 同口径）
        order = [i for i, _ in pairs]
        results = [results[i] for i in order if i < len(results)]
    t["rerank_ms"] = (time.perf_counter() - t0) * 1000 if results else None

    generated = False
    t["llm_ms"] = None
    if results and not skip_llm:
        ctx, adopted, _sources = build_prompt_and_sources(
            results, settings.RAG_MAX_CONTEXT_CHARS, settings.RAG_SNIPPET_CHARS
        )
        if adopted:
            t0 = time.perf_counter()
            llm.chat(build_system_prompt(), ctx)
            t["llm_ms"] = (time.perf_counter() - t0) * 1000
            generated = True

    t["total_ms"] = t["search_ms"] + (t["rerank_ms"] or 0) + (t["llm_ms"] or 0)
    t["generated"] = generated
    return t


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 链路性能基准")
    parser.add_argument("--rounds", type=int, default=3, help="全部问题跑几轮")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="跳过生成段（LLM API 不可达时先测检索/重排；报告会如实标注）",
    )
    args = parser.parse_args()

    print(
        f"基准开始：{args.rounds} 轮 × {len(QUESTIONS)} 问 "
        f"= {args.rounds * len(QUESTIONS)} 次完整链路（真实调用 rerank + LLM）"
    )

    db = new_session()
    user = db.query(User).filter_by(username="admin").first()
    if user is None:
        raise SystemExit("库里没有 admin 用户，先建一个再跑")

    rows: list[dict] = []
    for rnd in range(1, args.rounds + 1):
        for q in QUESTIONS:
            row = run_one_question(db, user, q, skip_llm=args.skip_llm)
            row["query"] = q
            row["round"] = rnd
            rows.append(row)
            print(
                f"  [轮{rnd}] {q[:14]:<16} "
                f"检索{row['search_ms']:6.0f}ms 重排{_fmt(row['rerank_ms']):>6}ms "
                f"生成{_fmt(row['llm_ms']):>7}ms 合计{row['total_ms']:7.0f}ms"
                f"{'' if row['generated'] else '（无依据，未调模型）' if not args.skip_llm else '（--skip-llm）'}"
            )

    seg = {
        "search_ms": [r["search_ms"] for r in rows],
        "rerank_ms": [r["rerank_ms"] for r in rows if r["rerank_ms"] is not None],
        "llm_ms": [r["llm_ms"] for r in rows if r["llm_ms"] is not None],
        "total_ms": [r["total_ms"] for r in rows],
    }
    stats = {k: _stats(v) for k, v in seg.items()}
    qps = len(rows) / (sum(seg["total_ms"]) / 1000)

    _write_report(stats, qps, len(rows), args.rounds, skipped_llm=args.skip_llm)
    print(f"\n串行单用户 QPS ≈ {qps:.2f}（{len(rows)} 次合计 {sum(seg['total_ms'])/1000:.1f}s）")
    if args.skip_llm:
        print("⚠️ 生成段未测（--skip-llm）—— 报告已如实标注，LLM API 恢复后重跑补数")
    print(f"报告已写入 docs/benchmark_{date.today().isoformat()}.md")


def _write_report(
    stats: dict, qps: float, n_total: int, rounds: int, skipped_llm: bool = False
) -> None:
    today = date.today().isoformat()
    lines = [
        f"# 性能基准（{today}）",
        "",
        f"串行、单用户、{rounds} 轮 × {len(QUESTIONS)} 问 = {n_total} 次完整链路，"
        "真实调用硅基流动（embedding/rerank）与 DeepSeek（生成）。",
        "分段口径与线上同一条链路（只调公开函数）。",
        "",
    ]
    if skipped_llm:
        lines += [
            "> ⚠️ **本次未测生成段**（`--skip-llm`）：跑基准时 DeepSeek API 不可达"
            "（单发调用 8 分钟无响应）。检索/重排两段数据有效；"
            "生成段与端到端数字待 API 恢复后重跑补上。",
            "",
        ]
    lines += [
        "| 段 | 次数 | 均值 | P50 | P95 | 最大 |",
        "|---|---|---|---|---|---|",
    ]
    labels = {
        "search_ms": "检索（向量化+双路召回+融合+取正文）",
        "rerank_ms": "重排（reranker）",
        "llm_ms": "生成（prompt + LLM）",
        "total_ms": "端到端（检索+重排，不含生成）" if skipped_llm else "端到端",
    }
    for key, label in labels.items():
        s = stats[key]
        if not s.get("n"):
            note = "（未测，见上方说明）" if key in ("llm_ms", "total_ms") else ""
            lines.append(f"| {label} | 0{note} | - | - | - | - |")
            continue
        lines.append(
            f"| {label} | {s['n']} | {s['mean']:.0f}ms | {s['p50']:.0f}ms "
            f"| {s['p95']:.0f}ms | {s['max']:.0f}ms |"
        )
    lines += [
        "",
        f"**串行单用户 QPS ≈ {qps:.2f}**（不含生成段；未做并发，多用户场景要另测，"
        "主要瓶颈在 LLM 生成段的排队）。"
        if skipped_llm else
        f"**串行单用户 QPS ≈ {qps:.2f}**（未做并发；多用户场景要另测，"
        "主要瓶颈在 LLM 生成段的排队）。",
        "",
        "复现：`.venv\\Scripts\\python.exe scripts\\benchmark.py`（需 VM 开机、.env 有效；"
        "LLM 不可达时加 `--skip-llm`）。",
        "",
    ]
    out = Path("docs") / f"benchmark_{today}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
