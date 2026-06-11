"""H1 盲评实验 harness — 设计预期-修正版 §四 H1 / §九 Phase V.

A 组 = 完整管线 (bootstrap 全采纳 → compress 全采纳 → run → 叙事报告);
B 组 = 同一模型、单次深度分析 prompt。
两组产出按确定性 seed 匿名化为 X/Y, 供盲评。
"""

from explain_engine.bench.harness import (
    counterintuitiveness,
    elicit_prior,
    run_baseline,
    run_bench,
    run_pipeline_headless,
)

__all__ = [
    "counterintuitiveness",
    "elicit_prior",
    "run_baseline",
    "run_bench",
    "run_pipeline_headless",
]
