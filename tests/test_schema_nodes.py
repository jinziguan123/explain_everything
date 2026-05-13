"""VariableNode schema test."""

import pytest
from pydantic import ValidationError

from explain_engine.schema.nodes import (
    AbstractionLevel,
    Epistemic,
    VariableNode,
)


class TestVariableNode:
    def test_minimal_concrete_node(self):
        node = VariableNode(
            id="n_001",
            name="房价上涨",
            description="一线城市房价持续高位",
            abstraction_level=0,
            confidence=0.9,
            epistemic="fact",
        )
        assert node.id == "n_001"
        assert node.abstraction_level == 0
        assert node.evidence_ids == []  # 默认空

    def test_abstract_node_with_evidence(self):
        node = VariableNode(
            id="n_010",
            name="长期不确定性",
            description="对未来收入 / 地位 / 价值的预期不可测",
            abstraction_level=2,
            confidence=0.7,
            epistemic="insight",
            evidence_ids=["e_001", "e_002"],
        )
        assert node.epistemic == "insight"
        assert node.evidence_ids == ["e_001", "e_002"]

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="n_002",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=1.5,  # >1
                epistemic="fact",
            )

    def test_abstraction_level_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="n_003",
                name="x",
                description="x",
                abstraction_level=5,  # 超过 2
                confidence=0.5,
                epistemic="fact",
            )

    def test_epistemic_literal_enforced(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="n_004",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=0.5,
                epistemic="wrong_label",  # type: ignore[arg-type]
            )

    def test_serializes_to_dict(self):
        node = VariableNode(
            id="n_001",
            name="房价上涨",
            description="x",
            abstraction_level=0,
            confidence=0.9,
            epistemic="fact",
        )
        d = node.model_dump()
        assert d["id"] == "n_001"
        assert d["evidence_ids"] == []

    def test_round_trip_json(self):
        node = VariableNode(
            id="n_001",
            name="房价",
            description="x",
            abstraction_level=1,
            confidence=0.6,
            epistemic="inference",
        )
        s = node.model_dump_json()
        restored = VariableNode.model_validate_json(s)
        assert restored == node

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="",  # 空 id 应该被拒
                name="x",
                description="x",
                abstraction_level=0,
                confidence=0.5,
                epistemic="fact",
            )

    def test_confidence_boundary_values_accepted(self):
        # 0.0 和 1.0 (闭区间端点) 都应该被接受
        for c in [0.0, 1.0]:
            node = VariableNode(
                id="n_x",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=c,
                epistemic="fact",
            )
            assert node.confidence == c

    def test_abstraction_level_all_values_accepted(self):
        for level in [0, 1, 2]:
            node = VariableNode(
                id=f"n_{level}",
                name="x",
                description="x",
                abstraction_level=level,  # type: ignore[arg-type]
                confidence=0.5,
                epistemic="fact",
            )
            assert node.abstraction_level == level
