"""Evidence — 证据条目 (Phase G, docs/设计预期-修正版.md §六).

接地管线为节点/边检索到的外部来源。证据是认识论地基:
节点/边通过 evidence_ids 引用, 证据本体存在 CognitiveState.evidence。
"""

from typing import Literal

from pydantic import BaseModel, Field

Stance = Literal["support", "contradict"]
# irrelevant 的来源不落盘 (噪音), 只保留有立场的证据。

EvidenceState = Literal["unverified", "verified", "contested"]
"""节点/边的存储级证据状态 (§六.2 状态机的持久化部分):
   - unverified: 未检索或检索无果 (默认; 展示为"假设"或推断, 由 tier 计算决定)
   - verified:   ≥2 独立来源支持且无冲突 → 展示为"实证"
   - contested:  检索到相互冲突的来源 → 展示为"争议", 保留两说
状态可迁移 (unverified + 新证据 → verified), 迁移历史即 evidence 列表本身。"""


class Evidence(BaseModel):
    """一条外部证据 (一个来源对一个声明的支持/反驳)。"""

    id: str = Field(min_length=1)
    claim: str
    """被检验的可检索断言 (由声明抽取改写, 非节点原文)。"""
    url: str
    title: str
    snippet: str
    stance: Stance
    retrieved_at: str
    """iso8601, 来源带时间戳 — 时间层 (§八 挂起项) 的预留接口。"""
