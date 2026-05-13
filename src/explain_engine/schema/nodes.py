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


class VariableNode(BaseModel):
    """认知图中的节点。"""

    id: str = Field(min_length=1)
    name: str
    description: str
    abstraction_level: AbstractionLevel
    confidence: float = Field(ge=0.0, le=1.0)
    epistemic: Epistemic
    evidence_ids: list[str] = Field(default_factory=list)

    model_config = {"frozen": False}  # MVP 可变，v0.2 可考虑 frozen + new_with()
