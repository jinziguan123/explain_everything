"""叙事层 (Narrative Layer) — 设计预期-修正版 §九 Phase V.

把收敛后的解释图渲染成第一等交付物"叙事报告":
- dossier.py: 纯函数, 从 CognitiveState 构建结构化图谱档案 (LLM 的唯一信息源)
- narrative.py: 调 LLM 严格基于档案写多层级叙事报告 (按 epistemic 分级措辞)
"""

from explain_engine.report.dossier import build_dossier, core_variables, graph_stats
from explain_engine.report.narrative import generate_report

__all__ = ["build_dossier", "core_variables", "generate_report", "graph_stats"]
