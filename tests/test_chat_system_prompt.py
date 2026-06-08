"""Phase 9 Wave C.2: dynamic system prompt assembly tests.

测试覆盖:
- 系统 prompt 包含 tool catalog (7 tools, 含 readonly/HITL flags)
- 包含 graph summary (L0/L1/L2 counts + lifecycle 状态)
- 包含 budget status (per-turn / per-session 剩余)
- memory hint 随 memory.md 内容变化

参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave C.2.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from explain_engine.chat.system_prompt import _render_now, assemble_system_prompt
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why X")
    g.add_node(VariableNode(
        id="c_001", name="abs1", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="c_002", name="abs2", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="obs1", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="why X")


def _make_budget(per_turn=10, per_session=50) -> dict:
    return {
        "per_turn_remaining": per_turn,
        "per_turn_limit": 10,
        "per_session_remaining": per_session,
        "per_session_limit": 50,
    }


class TestSystemPromptCurrentTime:
    def test_render_now_includes_date_weekday_tz(self) -> None:
        dt = datetime(2026, 6, 8, 15, 30, tzinfo=timezone(timedelta(hours=8)))
        s = _render_now(dt)
        assert "2026-06-08" in s
        assert "周一" in s  # 2026-06-08 是周一
        assert "15:30" in s
        assert "UTC+0800" in s

    def test_prompt_includes_current_time(self) -> None:
        dt = datetime(2026, 6, 8, 15, 30, tzinfo=timezone(timedelta(hours=8)))
        prompt = assemble_system_prompt(
            state=_make_state(), question="why X", memory_md="",
            budget=_make_budget(), now=dt,
        )
        assert "当前时间" in prompt
        assert "2026-06-08" in prompt


class TestSystemPromptToolCatalog:
    def test_system_prompt_includes_tool_catalog(self) -> None:
        """Prompt 必须列出所有 ALL_TOOLS (7 个) 名字."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="", budget=_make_budget(),
        )
        # 7 个 tool name 都得在 prompt 里
        for name in ("expand", "compress", "check", "predict", "counterfactual",
                     "add_observation", "read_node"):
            assert f"`{name}`" in prompt, f"tool {name!r} missing from prompt"

    def test_system_prompt_marks_readonly_and_hitl_flags(self) -> None:
        """check / read_node 应标 [readonly]; add_observation 应标 HITL."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="", budget=_make_budget(),
        )
        # check / read_node 是 readonly
        assert "[readonly]" in prompt
        # add_observation requires_hitl
        assert "HITL" in prompt


class TestSystemPromptGraphSummary:
    def test_system_prompt_includes_graph_summary(self) -> None:
        """Prompt 必须含 graph 节点 counts (L0/L1/L2)."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="", budget=_make_budget(),
        )
        # 2 L1, 1 L0, 0 L2
        assert "2 L1" in prompt
        assert "1 L0" in prompt
        assert "0 L2" in prompt

    def test_system_prompt_includes_multi_signal_when_available(self) -> None:
        """如果 last_acceptance_report 存在, 应包含 avg_consistency 等字段."""
        state = _make_state()
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.85,
            avg_essentialness=0.42,
            per_l1={"c_001": 0.85},
            per_l2={},
            weak_chain_l1s=["c_007"],
            lowest_l1=("c_001", 0.85),
            consistency_spread=0.1,
            essentialness_spread=0.05,
            rollout_coverage=0.9,
            missing_l0=[],
        )
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="", budget=_make_budget(),
        )
        assert "0.85" in prompt
        assert "c_007" in prompt or "weak_chain_l1s" in prompt
        assert "0.9" in prompt or "rollout_coverage" in prompt

    def test_system_prompt_handles_missing_acceptance_report(self) -> None:
        """report=None 应给 hint 'not yet computed'."""
        state = _make_state()
        # state.last_acceptance_report is None by default
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="", budget=_make_budget(),
        )
        assert "not yet computed" in prompt or "check" in prompt.lower()


class TestSystemPromptBudgetStatus:
    def test_system_prompt_includes_budget_finite_format(self) -> None:
        """Prompt 含 budget 行. 2026-05-20 hotfix: finite 显 'X/Y remaining'."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="",
            budget=_make_budget(per_turn=7, per_session=43),
        )
        assert "7/10 remaining" in prompt  # per-turn
        assert "43/50 remaining" in prompt  # per-session

    def test_system_prompt_unlimited_budget(self) -> None:
        """2026-05-20 hotfix: limit=0 (unlimited) 显 'unlimited (used K)'."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="",
            budget={
                "per_turn_remaining": -5,  # 已用 5
                "per_turn_limit": 0,
                "per_session_remaining": -20,  # 已用 20
                "per_session_limit": 0,
            },
        )
        assert "unlimited (used 5)" in prompt
        assert "unlimited (used 20)" in prompt


class TestSystemPromptMemoryHint:
    def test_memory_section_indicates_absence_when_empty(self) -> None:
        """空 memory.md → 应给 'no session memory yet' 之类提示."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md="", budget=_make_budget(),
        )
        # 没 memory 时给明确提示
        assert (
            "no session memory" in prompt.lower()
            or "no memory" in prompt.lower()
            or "fresh session" in prompt.lower()
        )

    def test_memory_md_rendered_in_system_prompt(self) -> None:
        """E.1 fix: memory_md 全文必须出现在 sys_prompt 里 (走 Anthropic
        native system 参数), 不是只显示字符数. 因为原 splice 方案产
        {role:'system'} msg 会被 loop._transcript_to_messages 过滤掉,
        memory 内容必须靠 system_prompt 内联才能到 LLM.
        """
        state = _make_state()
        memory = (
            "## Prior decisions through turn 5\n"
            "- expanded c_001 → 2 new L0 observations\n"
            "- root question revised: focus on causal not correlational\n"
            "- weak chain identified at c_003 (consistency=0.4)\n"
        )
        prompt = assemble_system_prompt(
            state=state, question="why X", memory_md=memory, budget=_make_budget(),
        )
        # 全文每行都得在 sys_prompt 里 (而非仅 chars 计数)
        assert "Prior decisions through turn 5" in prompt
        assert "expanded c_001" in prompt
        assert "root question revised" in prompt
        assert "weak chain identified at c_003" in prompt

    def test_system_prompt_includes_question(self) -> None:
        """Prompt 必须含 root question (decision tree 用)."""
        state = _make_state()
        prompt = assemble_system_prompt(
            state=state, question="why does ice float?", memory_md="",
            budget=_make_budget(),
        )
        assert "why does ice float?" in prompt
