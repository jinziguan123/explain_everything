"""CognitiveState — runtime 运行时状态。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2 + Phase 5 design §4。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from explain_engine.schema.evidence import Evidence
from explain_engine.schema.graph import ExplanationGraph

if TYPE_CHECKING:
    from explain_engine.engines.input_validation import InputAlignmentReport
    from explain_engine.engines.simulation import AcceptanceReport

Action = Literal["expand", "compress", "evaluate", "reflect"]
_VALID_ACTIONS = frozenset({"expand", "compress", "evaluate", "reflect"})

ReflectionAction = Literal[
    "continue", "re-expand", "expand-downward", "decay", "prune", "stop",
]
# Wave 1 (Phase 8) 加 "expand-downward". "re-expand" 保留供 backward compat
# (老 session JSON trace 反序列化时仍能落入 Literal 范围, 即使 reflect() 不再产生).
# Wave 4 (Phase 8) 加 "decay" — soft delete 低 fitness 节点.
# Note: action 字符串用 kebab-case (与 'continue'/'prune'/'re-expand' 对齐);
#       engine 函数名 expand_downward 用 snake_case (PEP-8). 不一致是有意.


@dataclass
class TraceEntry:
    """Phase 5 reasoning_trace 单条记录。"""

    tick: int
    action: Action
    target_node_id: str | None
    gain_delta: float
    llm_calls: int
    timestamp: str   # iso8601
    reflection_action: "ReflectionAction | None" = None

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"invalid action: {self.action!r}, must be one of {sorted(_VALID_ACTIONS)}"
            )
        if self.tick < 0:
            raise ValueError(f"tick must be >= 0, got {self.tick}")
        if self.llm_calls < 0:
            raise ValueError(f"llm_calls must be >= 0, got {self.llm_calls}")

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "action": self.action,
            "target_node_id": self.target_node_id,
            "gain_delta": self.gain_delta,
            "llm_calls": self.llm_calls,
            "timestamp": self.timestamp,
            "reflection_action": self.reflection_action,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEntry":
        return cls(
            tick=d["tick"],
            action=d["action"],
            target_node_id=d.get("target_node_id"),
            gain_delta=d["gain_delta"],
            llm_calls=d["llm_calls"],
            timestamp=d["timestamp"],
            reflection_action=d.get("reflection_action"),
        )


@dataclass
class CognitiveState:
    graph: ExplanationGraph
    budget_remaining: int
    root_question: str
    active_frontier: list[str] = field(default_factory=list)
    insight_candidates: list[str] = field(default_factory=list)
    tick: int = 0
    last_gain_tick: int = 0
    # Phase 5 NEW:
    last_gains: dict[str, float] = field(default_factory=dict)
    reasoning_trace: list[TraceEntry] = field(default_factory=list)
    # Phase 7 Wave C NEW:
    last_reflection_change_tick: int = 0
    # Phase 8 Wave 2 NEW: 缓存 aggregate report (in-memory; CLI / reflect 共用).
    # 不持久化到 JSON: (a) 避免序列化 dataclass 嵌套引用环, (b) 重算成本低,
    # (c) 非业务数据 — runtime 每个 reflect tick 重新刷新, load 后 None 即可.
    last_acceptance_report: "AcceptanceReport | None" = field(default=None, repr=False)
    # Phase 8 Wave 3 NEW: in-memory only (不持久化), 由 CLI explain run 入口
    # tick=0 时调 input_validation.validate() 写入. aggregate_acceptance 读取
    # 注入 input_alignment + falsifiable_reason 到 AcceptanceReport.
    # 同 last_acceptance_report 不持久化: tick=0 一次性事件, resume session 后
    # tick > 0 不会重跑, 留 None 即可 (acceptance 字段也跟着 None).
    last_input_alignment_report: "InputAlignmentReport | None" = field(
        default=None, repr=False
    )
    # Phase G NEW (设计预期-修正版 §六): 证据库。节点/边经 evidence_ids 引用。
    # 持久化 — 证据是认识论地基, 不是 runtime cache。
    evidence: dict[str, "Evidence"] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.budget_remaining < 0:
            raise ValueError(f"budget_remaining must be >= 0, got {self.budget_remaining}")
        if self.tick < 0:
            raise ValueError(f"tick must be >= 0, got {self.tick}")
        if self.last_gain_tick < 0:
            raise ValueError(f"last_gain_tick must be >= 0, got {self.last_gain_tick}")
        if self.last_reflection_change_tick < 0:
            raise ValueError(
                f"last_reflection_change_tick must be >= 0, got {self.last_reflection_change_tick}"
            )

    @classmethod
    def bootstrap(cls, question: str, budget: int) -> "CognitiveState":
        return cls(
            graph=ExplanationGraph(root_question=question),
            budget_remaining=budget,
            root_question=question,
        )

    def advance_tick(self) -> None:
        if self.budget_remaining <= 0:
            raise ValueError("budget exhausted")
        self.budget_remaining -= 1
        self.tick += 1

    def record_gain(self) -> None:
        self.last_gain_tick = self.tick

    def to_dict(self) -> dict:
        return {
            "graph": self.graph.to_dict(),
            "budget_remaining": self.budget_remaining,
            "root_question": self.root_question,
            "active_frontier": list(self.active_frontier),
            "insight_candidates": list(self.insight_candidates),
            "tick": self.tick,
            "last_gain_tick": self.last_gain_tick,
            "last_gains": dict(self.last_gains),
            "reasoning_trace": [e.to_dict() for e in self.reasoning_trace],
            "last_reflection_change_tick": self.last_reflection_change_tick,
            "evidence": {eid: ev.model_dump() for eid, ev in self.evidence.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        try:
            return cls(
                graph=ExplanationGraph.from_dict(d["graph"]),
                budget_remaining=d["budget_remaining"],
                root_question=d["root_question"],
                active_frontier=list(d.get("active_frontier", [])),
                insight_candidates=list(d.get("insight_candidates", [])),
                tick=d.get("tick", 0),
                last_gain_tick=d.get("last_gain_tick", 0),
                last_gains=dict(d.get("last_gains", {})),
                reasoning_trace=[
                    TraceEntry.from_dict(t) for t in d.get("reasoning_trace", [])
                ],
                last_reflection_change_tick=d.get("last_reflection_change_tick", 0),
                evidence={
                    eid: Evidence.model_validate(ev)
                    for eid, ev in d.get("evidence", {}).items()
                },
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid state dict: {exc}") from exc
