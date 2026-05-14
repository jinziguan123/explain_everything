"""Wave B.1: intervention_parser.parse 测试.

design §5.2. LLM-based 拆 intervention 为 existing_refs + new_concepts.
"""

import pytest

from explain_engine.engines.intervention_parser import (
    parse,
)
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="为什么宗教战争最血腥")
    g.add_node(VariableNode(
        id="c_001", name="绝对化价值框架", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_002", name="教义不可妥协性", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="神圣不可妥协性", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="为什么宗教战争最血腥",
    )


def _mock_llm(mocker, parsed_dict: dict | None, raise_validation: bool = False):
    """Mock llm.chat returning structured parsed output."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.parsed = parsed_dict
    if raise_validation:
        resp.parsed = {"foo": "bar"}  # invalid schema
    llm = mocker.AsyncMock()
    llm.chat = mocker.AsyncMock(return_value=resp)
    return llm


class TestParseExistingOnly:
    @pytest.mark.asyncio
    async def test_existing_refs_d_002(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": ["d_002"],
            "new_concepts": [],
        })
        result = await parse(state, "教义不可妥协性强化", llm)
        assert result.existing_refs == ["d_002"]
        assert result.new_concepts == []


class TestParseNewOnly:
    @pytest.mark.asyncio
    async def test_new_concept_only(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [{
                "name": "现代媒体放大效应",
                "description": "新概念",
                "expected_level": 2,
            }],
        })
        result = await parse(state, "现代媒体放大效应", llm)
        assert result.existing_refs == []
        assert len(result.new_concepts) == 1
        assert result.new_concepts[0].name == "现代媒体放大效应"
        assert result.new_concepts[0].expected_level == 2


class TestParseMixed:
    @pytest.mark.asyncio
    async def test_mixed_existing_and_new(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": ["d_002"],
            "new_concepts": [{
                "name": "现代媒体放大效应",
                "description": "d", "expected_level": 2,
            }],
        })
        result = await parse(state, "现代媒体 + 教义", llm)
        assert result.existing_refs == ["d_002"]
        assert len(result.new_concepts) == 1


class TestParseErrors:
    @pytest.mark.asyncio
    async def test_nonexistent_variable_id_raises_after_retry(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": ["d_999"],  # 不存在
            "new_concepts": [],
        })
        with pytest.raises(SchemaValidationError, match="d_999"):
            await parse(state, "x", llm)
        # 已 retry, 总共 2 次调用
        assert llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_too_many_new_concepts_raises(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [
                {"name": "a", "description": "d", "expected_level": 1},
                {"name": "b", "description": "d", "expected_level": 1},
                {"name": "c", "description": "d", "expected_level": 1},
            ],
        })
        with pytest.raises(SchemaValidationError):
            await parse(state, "x", llm)

    @pytest.mark.asyncio
    async def test_invalid_expected_level_raises(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [{
                "name": "a", "description": "d", "expected_level": 3,
            }],
        })
        with pytest.raises(SchemaValidationError):
            await parse(state, "x", llm)

    @pytest.mark.asyncio
    async def test_empty_parse_raises_valueerror(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [],
        })
        with pytest.raises(ValueError, match="无法解析"):
            await parse(state, "废话", llm)

    @pytest.mark.asyncio
    async def test_l0_node_in_existing_refs_raises(self, mocker) -> None:
        """L0 节点不可作 intervention target (Fix B)."""
        state = _make_state()  # has p_001 at level=0
        llm = _mock_llm(mocker, {
            "existing_refs": ["p_001"],  # L0 — 应被拒
            "new_concepts": [],
        })
        with pytest.raises(SchemaValidationError, match=r"L0|level"):
            await parse(state, "x", llm)
        # 应已 retry: 2 次 chat
        assert llm.chat.call_count == 2
