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

    stale_since_tick: int | None = Field(default=None)
    """Wave 4 Phase 8: 节点最后一次进入 stale 状态的 tick. None = 当前不是 stale.

    用于计算 stale → decayed 转换 (累积 STALE_TO_DECAYED_TICKS=5 后).
    Persisted to JSON to survive session save/load (修 review I1: in-memory
    dict 在 from_dict 后丢, stale 节点 resume 后永不 decay)."""

    # ── Phase G 证据状态 (设计预期-修正版 §六.2; 默认值 backward compat) ──
    evidence_state: Literal["unverified", "verified", "contested"] = "unverified"
    """接地管线写入: verified = ≥2 独立来源支持且无冲突; contested = 来源冲突。
    展示层 tier (实证/推断/假设/争议) 由 engines.grounding.compute_tiers
    结合图结构计算, 不在此存储。证据本体经 evidence_ids 指向 state.evidence。"""

    model_config = {"frozen": False}  # MVP 可变，v0.2 可考虑 frozen + new_with()
