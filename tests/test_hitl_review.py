"""Phase X2: /review (review_graph_async) + accept_all_insights tests.

默认全采纳后, /review 是事后修订入口: 列出现象/洞察, 按编号编辑/删除/新增,
编辑后 source 升级为 "user"。FakeLLM 模式 — 不触网不触 PG。
"""

from __future__ import annotations

import pytest
from rich.console import Console

from explain_engine.hitl.cli_interactive import (
    _parse_review_command,
    _review_items,
    accept_all_insights,
    format_review_listing,
    review_graph_async,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_provider(answers: list[str]):
    it = iter(answers)

    async def _provider(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration as e:
            raise AssertionError(
                f"input_provider 被多调一次 (prompt={prompt!r}); answers 不够"
            ) from e

    return _provider


def _state_with_graph() -> CognitiveState:
    """2 现象 (p_001/p_002) + 1 洞察 (c_001, manifests_as p_001)."""
    state = CognitiveState.bootstrap("为什么", budget=20)
    for i in (1, 2):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}", name=f"现象{i}", description=f"描述{i}",
                abstraction_level=0, confidence=0.7, epistemic="observation",
                source="llm",
            )
        )
    state.graph.add_node(
        VariableNode(
            id="c_001", name="模式A", description="洞察描述",
            abstraction_level=1, confidence=0.7, epistemic="insight",
            source="llm",
        )
    )
    state.graph.add_edge(
        RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7,
            mechanism_description="mech",
        )
    )
    return state


class TestReviewHelpers:
    def test_review_items_order_l0_then_l1(self):
        state = _state_with_graph()
        items = _review_items(state)
        assert items == [("p_001", 0), ("p_002", 0), ("c_001", 1)]

    def test_format_listing_numbers_and_user_marker(self):
        state = _state_with_graph()
        state.graph.replace_node(
            "p_002",
            state.graph.nodes["p_002"].model_copy(update={"source": "user"}),
        )
        listing = format_review_listing(state)
        assert "现象 (L0):" in listing
        assert "洞察 / 模式 (L1):" in listing
        assert "1. p_001" in listing
        assert "2. p_002 [user]" in listing  # user 来源标记
        assert "3. c_001" in listing

    def test_format_listing_empty(self):
        state = CognitiveState.bootstrap("q", budget=10)
        assert "暂无现象" in format_review_listing(state)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", ("e", 1)),
            ("e2", ("e", 2)),
            ("e 2", ("e", 2)),
            ("d3", ("d", 3)),
            ("d 3", ("d", 3)),
            ("abc", ("e", None)),
        ],
    )
    def test_parse_review_command(self, raw, expected):
        assert _parse_review_command(raw) == expected


class TestReviewGraphAsync:
    @pytest.mark.asyncio
    async def test_no_input_provider_readonly(self):
        """input_provider=None → 仅渲染, 不改 graph, 返 False."""
        state = _state_with_graph()
        changed = await review_graph_async(
            state, None, console=Console(file=None)
        )
        assert changed is False
        assert len(state.graph.nodes) == 3

    @pytest.mark.asyncio
    async def test_quit_immediately_no_change(self):
        state = _state_with_graph()
        provider = _make_provider(["q"])
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is False

    @pytest.mark.asyncio
    async def test_edit_by_number_upgrades_source(self):
        """编号 1 编辑 p_001 名称 + 描述 → source=user."""
        state = _state_with_graph()
        # 编号1 → 编辑, 新名称, 新描述, 然后 q
        provider = _make_provider(["1", "新名称", "新描述", "q"])
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is True
        node = state.graph.nodes["p_001"]
        assert node.name == "新名称"
        assert node.description == "新描述"
        assert node.source == "user"

    @pytest.mark.asyncio
    async def test_edit_empty_keeps_original_no_source_change(self):
        state = _state_with_graph()
        provider = _make_provider(["1", "", "  ", "q"])  # 两项都空
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is False
        node = state.graph.nodes["p_001"]
        assert node.name == "现象1"
        assert node.source == "llm"  # 不升级

    @pytest.mark.asyncio
    async def test_delete_by_number_cascades(self):
        """d3 删 c_001 → node + edge 一起删."""
        state = _state_with_graph()
        provider = _make_provider(["d3", "q"])
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is True
        assert "c_001" not in state.graph.nodes
        assert "e_001" not in state.graph.edges

    @pytest.mark.asyncio
    async def test_add_phenomenon_source_user(self):
        """a 新增现象 → 新 L0, id 顺延 p_003, source=user."""
        state = _state_with_graph()
        provider = _make_provider(["a", "新现象", "新现象描述", "q"])
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is True
        assert "p_003" in state.graph.nodes
        new = state.graph.nodes["p_003"]
        assert new.name == "新现象"
        assert new.abstraction_level == 0
        assert new.source == "user"

    @pytest.mark.asyncio
    async def test_add_cancel_with_q(self):
        state = _state_with_graph()
        provider = _make_provider(["a", "q", "q"])  # 名称输 q → 取消, 再 q 退出
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is False
        assert "p_003" not in state.graph.nodes

    @pytest.mark.asyncio
    async def test_bad_index_then_quit(self):
        state = _state_with_graph()
        provider = _make_provider(["99", "q"])  # 越界编号
        changed = await review_graph_async(
            state, provider, console=Console(file=None)
        )
        assert changed is False


class TestAcceptAllInsights:
    def test_clears_candidates_returns_count(self):
        state = _state_with_graph()
        state.insight_candidates = ["c_001"]
        n = accept_all_insights(state, console=Console(file=None))
        assert n == 1
        assert state.insight_candidates == []
        # L1 仍在 graph
        assert "c_001" in state.graph.nodes

    def test_empty_candidates(self):
        state = _state_with_graph()
        state.insight_candidates = []
        n = accept_all_insights(state, console=Console(file=None))
        assert n == 0
