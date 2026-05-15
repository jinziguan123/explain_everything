"""VariableNode — explain engine 的认知原子。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

from typing import Literal

from pydantic import BaseModel, Field

Epistemic = Literal[
    "fact",          # 可验证事实（有数据 / 共识）
    "observation",   # 主观可观察（用户自述 / 现象描述）
    "inference",     # 基于已知关系的推断
    "insight",       # 抽象跃迁后的解释性变量
    "speculation",   # 弱推断 / 不确定
]

AbstractionLevel = Literal[0, 1, 2]
# 0 = concrete (房价上涨)
# 1 = mid       (经济压力)
# 2 = abstract  (长期不确定性)

Source = Literal["llm", "user"]
# llm  = LLM 生成（默认）
# user = HITL 用户 add / edit 过的

LifecycleState = Literal["active", "stale", "decayed"]
"""Wave 4 Phase 8: 节点生命阶段:
   - active: 正常参与 simulation / expand / reflect
   - stale: fitness 长期低, 候选 decay (仍参与 simulation, 仅 reflect 提示)
   - decayed: fitness 极低且超时, 不参与 simulation / expand, trace 保留 (soft delete)
"""


class VariableNode(BaseModel):
    """认知图中的节点。"""

    id: str = Field(min_length=1)
    name: str
    description: str
    abstraction_level: AbstractionLevel
    confidence: float = Field(ge=0.0, le=1.0)
    epistemic: Epistemic
    evidence_ids: list[str] = Field(default_factory=list)
    source: Source = "llm"

    # ── Wave 4 Phase 8 lifecycle 字段 (全部默认值, backward compat 自动) ──
    activation: float = Field(default=1.0, ge=0.0, le=1.0)
    """当前激活度. Birth 时 1.0, decay 时降低. simulation/expand 触达时刷新."""

    stability: float = Field(default=0.0, ge=0.0, le=1.0)
    """稳定性. 重复被 expand/reflect 触达累加. 用作 fitness 加分项."""

    last_used_tick: int = Field(default=0, ge=0)
    """最后被 simulation/reflect/expand 触达的 tick. 配合 age_ticks 算"陈旧度"."""

    age_ticks: int = Field(default=0, ge=0)
    """总存活 tick 数."""

    lifecycle_state: LifecycleState = "active"

    model_config = {"frozen": False}  # MVP 可变，v0.2 可考虑 frozen + new_with()
