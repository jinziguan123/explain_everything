from datetime import date, datetime
from typing import Literal, TypedDict
from uuid import uuid4

from explain_agent.core.types import Evidence


class DimensionResult(TypedDict):
    evidence: list[Evidence]
    mini_summary: str
    retry_count: int
    no_data: bool
    confidence: Literal["high", "medium", "low"]


class SubBranchSpec(TypedDict):
    name: str
    query_hints: list[str]


class Citation(TypedDict):
    evidence_id: str
    url: str | None
    snapshot_id: str | None
    source_type: str


class AttributionState(TypedDict, total=False):
    # 输入
    raw_question: str
    asked_at: datetime
    session_id: str

    # parse
    target: str
    time_window: tuple[date, date]
    intent: Literal["up", "down", "volatile", "general"]

    # router/framework
    domain_id: str
    framework: dict

    # 客观锚点
    market_facts: dict

    # 维度结果
    dimension_results: dict[str, DimensionResult]

    # 动态扩展
    needs_subbranch: bool
    subbranches: list[SubBranchSpec]
    subbranch_results: dict[str, DimensionResult]

    # 最终输出
    narrative: str
    dimension_reports: dict[str, str]
    citations: list[Citation]
    confidence: Literal["high", "medium", "low"]

    # 元数据
    llm_calls: dict[str, int]
    total_cost: float
    errors: list[str]


def new_attribution_state(raw_question: str, session_id: str | None = None) -> AttributionState:
    return {
        "raw_question": raw_question,
        "asked_at": datetime.now(),
        "session_id": session_id or f"s_{uuid4().hex[:8]}",
        "dimension_results": {},
        "subbranches": [],
        "subbranch_results": {},
        "needs_subbranch": False,
        "narrative": "",
        "dimension_reports": {},
        "citations": [],
        "confidence": "medium",
        "llm_calls": {},
        "total_cost": 0.0,
        "errors": [],
    }
