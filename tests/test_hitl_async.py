"""Phase 11 Wave 2: review_phenomena_async + review_insights_async tests.

跟 sync review_phenomena / review_insights 对齐 (k/e/d), async 版加 'q' quit
+ edit 仅改 description.
"""

import pytest
from rich.console import Console

from explain_engine.hitl.cli_interactive import (
    review_insights_async,
    review_phenomena_async,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(id_: str, name: str = "x", desc: str = "x") -> VariableNode:
    return VariableNode(
        id=id_,
        name=name,
        description=desc,
        abstraction_level=0,
        confidence=0.7,
        epistemic="observation",
    )


def _make_provider(answers: list[str]):
    """生成 async input_provider, 按序返预设回答."""
    it = iter(answers)

    async def _provider(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration as e:
            raise AssertionError(
                f"input_provider 被多调用一次 (prompt={prompt!r}); "
                "answers list 不够"
            ) from e

    return _provider


class TestReviewPhenomenaAsync:
    @pytest.mark.asyncio
    async def test_no_input_provider_returns_all(self):
        """input_provider=None → 返全 phenomena (accept all, test/fallback path)."""
        phenomena = [_node("p_001", "A"), _node("p_002", "B")]
        result = await review_phenomena_async(phenomena, None)
        assert len(result) == 2
        assert [n.id for n in result] == ["p_001", "p_002"]

    @pytest.mark.asyncio
    async def test_no_input_provider_returns_new_list(self):
        """返新 list, 不 mutate caller 原 list."""
        phenomena = [_node("p_001")]
        result = await review_phenomena_async(phenomena, None)
        assert result is not phenomena

    @pytest.mark.asyncio
    async def test_keep_all_with_k(self):
        """全 'k' → 返全 phenomena."""
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        provider = _make_provider(["k", "k", "k"])
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert [n.id for n in result] == ["p_001", "p_002", "p_003"]

    @pytest.mark.asyncio
    async def test_drop_all_with_d(self):
        """全 'd' → 返 []."""
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        provider = _make_provider(["d", "d", "d"])
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_edit_changes_description(self):
        """'e' + new desc → phenomenon.description 更新, name 不变."""
        phenomena = [_node("p_001", name="原名", desc="原描述")]
        provider = _make_provider(["e", "新描述"])
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert len(result) == 1
        assert result[0].description == "新描述"
        assert result[0].name == "原名"
        assert result[0].id == "p_001"

    @pytest.mark.asyncio
    async def test_edit_empty_desc_keeps_original(self):
        """'e' + 空 desc → 保留原 description."""
        phenomena = [_node("p_001", desc="原描述")]
        provider = _make_provider(["e", "   "])  # 空白
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert result[0].description == "原描述"

    @pytest.mark.asyncio
    async def test_q_quits_keeps_processed(self):
        """中途 'q' → break, 返已 process 的 kept (不含未 process)."""
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        provider = _make_provider(["k", "q"])  # p_001 keep, p_002 quit, p_003 不到
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert [n.id for n in result] == ["p_001"]

    @pytest.mark.asyncio
    async def test_default_k_when_unknown_action(self):
        """unknown action (e.g. 'x' / 空) → default keep."""
        phenomena = [_node("p_001"), _node("p_002")]
        provider = _make_provider(["x", ""])
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert [n.id for n in result] == ["p_001", "p_002"]

    @pytest.mark.asyncio
    async def test_mixed_kde(self):
        """混合 k/d/e — 验证组合."""
        phenomena = [
            _node("p_001", desc="d1"),
            _node("p_002", desc="d2"),
            _node("p_003", desc="d3"),
        ]
        provider = _make_provider(["k", "d", "e", "edited"])
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert [n.id for n in result] == ["p_001", "p_003"]
        assert result[1].description == "edited"

    @pytest.mark.asyncio
    async def test_eof_during_action_breaks(self):
        """input_provider 抛 EOFError → break, 返已 process kept."""
        phenomena = [_node("p_001"), _node("p_002")]

        async def provider(prompt: str) -> str:
            # Phase 15: prompt 中文化 (从 "[k]eep" → "[k] 保留")
            if "[k] 保留" in prompt or "[k]eep" in prompt:
                # 第二次 (p_002) 抛 EOF
                provider.calls += 1  # type: ignore[attr-defined]
                if provider.calls == 2:  # type: ignore[attr-defined]
                    raise EOFError
                return "k"
            return "k"

        provider.calls = 0  # type: ignore[attr-defined]
        result = await review_phenomena_async(
            phenomena, provider, console=Console(file=None)
        )
        assert [n.id for n in result] == ["p_001"]


def _build_state_with_candidates() -> CognitiveState:
    """构造 3 个 candidate (c_001..c_003) + 2 个 concrete phenomena (p_001/p_002).

    跟 test_hitl_cli_interactive_insights.py 的 fixture 对齐.
    """
    state = CognitiveState.bootstrap("q", budget=20)
    for i in (1, 2):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"p{i}",
                description="",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    for i, cid in enumerate(["c_001", "c_002", "c_003"], start=1):
        state.graph.add_node(
            VariableNode(
                id=cid,
                name=f"abs_{i}",
                description=f"def_{i}",
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
                source="llm",
            )
        )
        state.graph.add_edge(
            RelationEdge(
                id=f"e_{i:03d}",
                source_node=cid,
                target_node="p_001",
                relation_type="manifests_as",
                confidence=0.7,
                mechanism_description=f"mech_{i}",
            )
        )
    state.insight_candidates = ["c_001", "c_002", "c_003"]
    state.last_gains = {"c_001": 0.6, "c_002": 0.5, "c_003": 0.3}
    return state


class TestReviewInsightsAsync:
    @pytest.mark.asyncio
    async def test_no_input_provider_keeps_all(self):
        """input_provider=None → 不交互, 全 keep, 清 insight_candidates."""
        state = _build_state_with_candidates()
        await review_insights_async(state, None, console=Console(file=None))
        # 3 个 abstract 仍在
        assert sum(
            1 for n in state.graph.nodes.values() if n.abstraction_level == 1
        ) == 3
        # candidates 清空 (跟 sync 完成态对齐)
        assert state.insight_candidates == []

    @pytest.mark.asyncio
    async def test_keep_all(self):
        """全 'k' → 全 keep."""
        state = _build_state_with_candidates()
        provider = _make_provider(["k", "k", "k"])
        await review_insights_async(state, provider, console=Console(file=None))
        assert sum(
            1 for n in state.graph.nodes.values() if n.abstraction_level == 1
        ) == 3
        assert state.insight_candidates == []

    @pytest.mark.asyncio
    async def test_drop_one_cascades_edges(self):
        """drop c_002 → node + e_002 (manifests_as edge) 一起删 + last_gains 清."""
        state = _build_state_with_candidates()
        provider = _make_provider(["k", "d", "k"])
        await review_insights_async(state, provider, console=Console(file=None))
        assert "c_002" not in state.graph.nodes
        assert "e_002" not in state.graph.edges
        assert "c_001" in state.graph.nodes
        assert "c_003" in state.graph.nodes
        assert "c_002" not in state.last_gains

    @pytest.mark.asyncio
    async def test_drop_all(self):
        """全 'd' → 0 abstract 保留, 触发 WARN 分支."""
        state = _build_state_with_candidates()
        provider = _make_provider(["d", "d", "d"])
        await review_insights_async(state, provider, console=Console(file=None))
        assert sum(
            1 for n in state.graph.nodes.values() if n.abstraction_level == 1
        ) == 0

    @pytest.mark.asyncio
    async def test_edit_updates_description_and_source(self):
        """edit c_001 → description 改 + source = 'user'."""
        state = _build_state_with_candidates()
        provider = _make_provider(["e", "新 description", "k", "k"])
        await review_insights_async(state, provider, console=Console(file=None))
        node = state.graph.nodes["c_001"]
        assert node.description == "新 description"
        assert node.source == "user"
        # name 不变 (async 版仅 edit description)
        assert node.name == "abs_1"

    @pytest.mark.asyncio
    async def test_edit_empty_desc_keeps_original(self):
        """edit + 空 desc → 不调 replace_node, node 不变."""
        state = _build_state_with_candidates()
        provider = _make_provider(["e", "", "k", "k"])
        await review_insights_async(state, provider, console=Console(file=None))
        node = state.graph.nodes["c_001"]
        assert node.description == "def_1"  # 原值
        assert node.source == "llm"  # 不升级

    @pytest.mark.asyncio
    async def test_quit_keeps_remaining_candidates(self):
        """中途 'q' → break, insight_candidates 仍保留 (不清空)."""
        state = _build_state_with_candidates()
        provider = _make_provider(["k", "q"])
        await review_insights_async(state, provider, console=Console(file=None))
        # 取消时不清空 candidates (用户可再次 review)
        assert state.insight_candidates == ["c_001", "c_002", "c_003"]
        # c_002 / c_003 仍 in graph (未 drop)
        assert "c_002" in state.graph.nodes
        assert "c_003" in state.graph.nodes
