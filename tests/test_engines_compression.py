"""CompressionEngine.propose_candidates 测试。"""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.compression import propose_candidates
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _setup_state(n_concrete: int = 12) -> CognitiveState:
    state = CognitiveState.bootstrap("为什么年轻人不消费", budget=20)
    for i in range(1, n_concrete + 1):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"现象{i}",
                description=f"现象{i}描述",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    return state


def _candidate(name: str, coverage_ids: list[str]) -> dict:
    return {
        "name": name,
        "description": f"{name}的定义",
        "coverage": [
            {"concrete_id": cid, "mechanism": f"{name} → {cid}"}
            for cid in coverage_ids
        ],
    }


def _mock_llm_response(candidates: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"candidates": candidates},
        model="test",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
class TestPropose:
    async def test_basic_5_candidates(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("长期不确定性", [f"p_{i:03d}" for i in range(1, 10)]),
            _candidate("社会竞争结构", [f"p_{i:03d}" for i in range(2, 9)]),
            _candidate("生活成本上涨", [f"p_{i:03d}" for i in range(1, 6)]),
            _candidate("传统价值观瓦解", [f"p_{i:03d}" for i in range(5, 9)]),
            _candidate("技术替代消费", [f"p_{i:03d}" for i in range(4, 7)]),
        ])

        await propose_candidates(state, llm)

        # 5 abstract nodes 灌入 graph
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 5
        assert all(n.id.startswith("c_") for n in abstracts)
        assert all(n.epistemic == "insight" for n in abstracts)
        assert all(n.source == "llm" for n in abstracts)
        # insight_candidates 列出 5 个 c_id
        assert sorted(state.insight_candidates) == [f"c_{i:03d}" for i in range(1, 6)]

    async def test_truncate_over_5(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate(f"abs_{i}", ["p_001", "p_002"]) for i in range(8)
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 5

    async def test_accept_3_warn_on_low(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate(f"abs_{i}", ["p_001", "p_002"]) for i in range(3)
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 3

    async def test_drop_coverage_below_2(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("good_1", ["p_001", "p_002"]),
            _candidate("solo", ["p_003"]),  # 只 1 个 coverage，应淘汰
            _candidate("good_2", ["p_004", "p_005"]),
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 2
        assert {n.name for n in abstracts} == {"good_1", "good_2"}

    async def test_invalid_concrete_id_raises(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        bad = _mock_llm_response([
            _candidate("bad", ["p_999", "p_888"]),  # 不存在的 id
        ])
        llm.chat.side_effect = [bad, bad]  # 显式两次坏
        with pytest.raises(SchemaValidationError, match="concrete_id"):
            await propose_candidates(state, llm)
        assert llm.chat.await_count == 2

    async def test_retry_succeeds_on_second_attempt(self) -> None:
        """第一次返坏数据 → retry → 第二次返好数据 → 成功。"""
        state = _setup_state()
        llm = AsyncMock()
        bad = _mock_llm_response([_candidate("bad", ["p_999"])])  # 坏 id
        good = _mock_llm_response([_candidate("good", ["p_001", "p_002"])])
        llm.chat.side_effect = [bad, good]
        await propose_candidates(state, llm)
        assert llm.chat.await_count == 2
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 1
        assert abstracts[0].name == "good"

    async def test_empty_mechanism_raises(self) -> None:
        """LLM 返空 mechanism → schema 校验失败 → retry → raise."""
        state = _setup_state()
        llm = AsyncMock()
        bad_resp = _mock_llm_response([{
            "name": "x", "description": "x",
            "coverage": [{"concrete_id": "p_001", "mechanism": "   "}],
        }])
        llm.chat.side_effect = [bad_resp, bad_resp]
        with pytest.raises(SchemaValidationError):
            await propose_candidates(state, llm)

    async def test_edge_id_continues_from_existing(self) -> None:
        """已有 e_005 时，新 edge 从 e_006 起。"""
        from explain_engine.schema.edges import RelationEdge
        state = _setup_state()
        # 手动 add 一条 e_005
        state.graph.add_node(
            VariableNode(
                id="c_pre", name="pre", description="",
                abstraction_level=1, confidence=0.7, epistemic="insight",
            )
        )
        state.graph.add_edge(
            RelationEdge(
                id="e_005", source_node="c_pre", target_node="p_001",
                relation_type="manifests_as", confidence=0.7,
                mechanism_description="pre",
            )
        )
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_002", "p_003"]),
        ])
        await propose_candidates(state, llm)
        # 新 edge 应从 e_006 起
        new_edge_ids = [
            eid for eid in state.graph.edges if eid.startswith("e_") and eid != "e_005"
        ]
        assert sorted(new_edge_ids) == ["e_006", "e_007"]
        # c_id 仍从 1 起（c_pre 不匹配 c_NNN 数字规则）
        new_c = [
            nid for nid in state.graph.nodes
            if nid.startswith("c_") and nid != "c_pre"
        ]
        assert new_c == ["c_001"]

    async def test_candidate_id_continues_from_existing(self) -> None:
        """已有 c_002 时，新 candidate 从 c_003 起（虽然实际 CLI 不会这样跑，但 invariant 要保证）。"""
        state = _setup_state()
        state.graph.add_node(
            VariableNode(
                id="c_002", name="prev", description="",
                abstraction_level=1, confidence=0.7, epistemic="insight",
            )
        )
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_001", "p_002"]),
        ])
        await propose_candidates(state, llm)
        new_c = [
            nid for nid in state.graph.nodes
            if nid.startswith("c_") and nid != "c_002"
        ]
        assert new_c == ["c_003"]

    async def test_coverage_overlap_allowed(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("a", ["p_001", "p_002"]),
            _candidate("b", ["p_001", "p_003"]),  # p_001 被两个 abstract 覆盖
        ])
        await propose_candidates(state, llm)
        # p_001 有 2 条 incoming manifests_as
        incoming_count = sum(
            1 for e in state.graph.edges.values()
            if e.target_node == "p_001" and e.relation_type == "manifests_as"
        )
        assert incoming_count == 2

    async def test_edges_created_with_mechanism(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_001", "p_002"]),
        ])
        await propose_candidates(state, llm)
        x_edges = [e for e in state.graph.edges.values() if e.source_node.startswith("c_")]
        assert len(x_edges) == 2
        assert all(e.relation_type == "manifests_as" for e in x_edges)
        assert all(e.mechanism_description for e in x_edges)


class TestRenderLexiconTopKSection:
    """Phase 13 Wave 3 Task 1: _render_lexicon_topk_section helper."""

    def test_none_returns_empty(self) -> None:
        from explain_engine.engines.compression import _render_lexicon_topk_section
        assert _render_lexicon_topk_section(None) == ""

    def test_empty_list_returns_empty(self) -> None:
        from explain_engine.engines.compression import _render_lexicon_topk_section
        assert _render_lexicon_topk_section([]) == ""

    def test_non_empty_renders_section(self) -> None:
        from explain_engine.engines.compression import _render_lexicon_topk_section
        top_k = [
            ("v_aaaa1111", "经济不安全感导致防御性储蓄上升", 5),
            ("v_bbbb2222", "老龄化人口结构演变", 3),
        ]
        section = _render_lexicon_topk_section(top_k)
        # Header present (hotfix 2026-05-20: 改 "复用 ID 而非新生" → "提不同角度"
        # 因前者与 candidate JSON schema 冲突触发 SchemaValidationError)
        assert "已知 lexicon abstractions" in section
        assert "不同角度" in section
        assert "避免" in section
        # Should NOT have old conflicting wording
        assert "复用 ID" not in section
        # Both entries listed with id + reused N x + canonical text
        assert "v_aaaa1111" in section
        assert "经济不安全感" in section
        assert "reused 5x" in section
        assert "v_bbbb2222" in section
        assert "老龄化" in section
        assert "reused 3x" in section

    def test_long_canonical_truncated(self) -> None:
        """canonical 截 80 char + ..."""
        from explain_engine.engines.compression import _render_lexicon_topk_section
        long_canonical = "y" * 100
        top_k = [("v_aaaa1111", long_canonical, 1)]
        section = _render_lexicon_topk_section(top_k)
        assert "y" * 80 + "..." in section


@pytest.mark.asyncio
class TestProposeCandidatesPromptIncludesLexicon:
    """Phase 13 Wave 3 Task 1: propose_candidates 传 Top-K section 进 prompt."""

    async def test_prompt_contains_top_k_when_lexicon_provided(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_001", "p_002"]),
        ])
        existing_lexicon = [
            ("v_aaaa1111", "经济不安全感", 5),
            ("v_bbbb2222", "老龄化结构", 3),
        ]
        await propose_candidates(
            state=state,
            llm=llm,
            existing_lexicon=existing_lexicon,
        )
        # 取 LLM 收到的 user message
        call_args = llm.chat.await_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        user_msg = messages[1].content
        assert "已知 lexicon abstractions" in user_msg
        assert "不同角度" in user_msg
        assert "v_aaaa1111" in user_msg
        assert "v_bbbb2222" in user_msg
        assert "reused 5x" in user_msg
        # Hotfix 2026-05-20: NOT have "复用 ID" (conflicts with JSON schema)
        assert "复用 ID" not in user_msg

    async def test_prompt_omits_section_when_lexicon_none(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_001", "p_002"]),
        ])
        await propose_candidates(state=state, llm=llm)
        call_args = llm.chat.await_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        user_msg = messages[1].content
        assert "已知 lexicon abstractions" not in user_msg
