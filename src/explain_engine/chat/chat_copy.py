"""Phase 15: chat REPL user-facing 中文文案集中点.

所有 chat REPL 路径 (slash_commands / slash_stage_rules / cli._render_event /
hitl/cli_interactive) 引此 module 的常量 / 模板, 不再硬编中文/英文.
单一改文案 source — 改一处全场动.
"""

from __future__ import annotations


TERMS_MAP: dict[str, str] = {
    # ── Graph 节点层次 ──
    "L0": "现象",
    "L1": "归纳出的模式",
    "L2": "深层原因",
    "abstraction": "归纳",
    "driver": "深层原因",
    "observation": "现象",

    # ── Graph 结构 ──
    "graph": "因果图",
    "node": "节点",
    "edge": "因果关系",
    "weak_chain": "薄弱的因果链",
    "manifests_as": "体现为",
    "causes": "导致",

    # ── Runtime ops ──
    "propagation": "影响传导",
    "expansion": "扩展",
    "reflection": "回顾反思",
    "decay": "衰减",
    "reasoning loop": "推理循环",
    "tick": "推理步",

    # ── Cross-session ──
    "lexicon": "跨 session 概念库",
    "canonical_mechanism": "标准化的机制描述",
    "global_id": "全局 ID",

    # ── Multi-signal acceptance ──
    "multi-signal": "多信号",
    "consistency": "一致性",
    "essentialness": "本质重要性",
    "rollout_coverage": "覆盖率",
    "acceptance report": "接受度评估",

    # ── Stage (session.meta.stage 4 个值) ──
    "bootstrap_pending": "等待启动",
    "insight_pending":   "等待审查",
    "done":              "已归纳",
    "converged":         "已推理",

    # ── 引擎 ops ──
    "propose_candidates": "提候选",
    "score_all": "评分",
    "HITL": "人工审查",
    "persist": "存盘",
}


def zh(term: str) -> str:
    """把英文/技术词翻成中文直观短语. 找不到返原词 (defensive)."""
    return TERMS_MAP.get(term, term)
