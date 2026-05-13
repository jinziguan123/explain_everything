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

    id: str
    source_node: str
    target_node: str
    relation_type: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    mechanism_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _no_self_loop(self) -> "RelationEdge":
        if self.source_node == self.target_node:
            raise ValueError(f"self-loop not allowed: {self.source_node}")
        return self
