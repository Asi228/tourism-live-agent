"""RRF（Reciprocal Rank Fusion）：把多路召回的名次融合成一个统一排序。

公式：`score(d) = Σ_lists 1 / (k + rank_l(d))`，rank 从 1 开始，k 是平滑常数（经典值 60）。

**为什么用 RRF 而不是"分数归一化后加权求和"**：两条路的分数**不可比** ——
向量路是余弦相似度，关键词路是命中词覆盖率，硬归一化到同一量纲会引入拍脑袋的权重。
RRF 只看**名次**：一条结果在某一路排得越靠前贡献越大，两路都靠前就最高 ——
无需调参即可工作，是工程上最稳的多路融合方式（也是业界检索系统的常用默认）。
"""

CLASSIC_K = 60


def rrf_merge(ranked_lists: list[list[int]], k: int = CLASSIC_K) -> list[tuple[int, float]]:
    """融合同一个 id 空间上的多路有序列表 → `[(id, rrf_score)]`，按分降序。

    - 空路直接忽略（某条路没结果不影响另一条）—— 所以"关键词路空跑"
      对融合是透明的：结果退化成向量路原顺序，不是错误；
    - 同一个 id 出现在多路 → 分数**累加**（这就是"两路都认可它"的体现）；
    - 输出顺序**确定性**（同分按 id 升序兜底）—— 测试与回放才有稳定结果。
    """
    scores: dict[int, float] = {}
    for ids in ranked_lists:
        for rank, item_id in enumerate(ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
