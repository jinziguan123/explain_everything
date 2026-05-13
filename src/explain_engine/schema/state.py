"""CognitiveState — runtime 运行时状态。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

from dataclasses import dataclass, field

from explain_engine.schema.graph import ExplanationGraph


@dataclass
class CognitiveState:
    graph: ExplanationGraph
    budget_remaining: int
    root_question: str
    active_frontier: list[str] = field(default_factory=list)
    insight_candidates: list[str] = field(default_factory=list)
    tick: int = 0
    last_gain_tick: int = 0

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
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid state dict: {exc}") from exc
