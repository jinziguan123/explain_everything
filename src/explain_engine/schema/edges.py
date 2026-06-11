"""RelationEdge — explain graph 中的边。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

RelationType = Literal[
    "causes",         # X 生成 Y
    "amplifies",      # X 加剧 Y
    "suppresses",     # X 抑制 Y
    "constrains",     # X 限制 Y
    "manifests_as",   # X 在具体层表现为 Y（抽象→具体专用）
]


class RelationEdge(BaseModel):
    """认知图中的有向边。"""

    id: str = Field(min_length=1)
    source_node: str = Field(min_length=1)
    target_node: str = Field(min_length=1)
    relation_type: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    """有效置信 (propagation / CV 直接使用)。接地后 = base(tier) × llm_confidence
    (设计预期-修正版 §五.3); 未接地的旧边保持 LLM 原始值不变。"""
    mechanism_description: str = Field(min_length=1)

    # ── Phase G 证据字段 (设计预期-修正版 §五.3 / §六.2; 默认值 backward compat) ──
    evidence_state: Literal["unverified", "verified", "contested"] = "unverified"
    evidence_ids: list[str] = Field(default_factory=list)
    llm_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    """LLM 原始合理性置信 (plausibility 映射)。None = 尚未应用接地公式。
    接地公式可重复应用 (幂等): 始终从 llm_confidence 重算 confidence。"""

    @model_validator(mode="after")
    def _no_self_loop(self) -> "RelationEdge":
        if self.source_node == self.target_node:
            raise ValueError(f"self-loop not allowed: {self.source_node}")
        return self
