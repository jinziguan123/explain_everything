"""RelationEdge schema test."""

import pytest
from pydantic import ValidationError

from explain_engine.schema.edges import RelationEdge, RelationType


class TestRelationEdge:
    def test_minimal_edge(self):
        edge = RelationEdge(
            id="e_001",
            source_node="n_010",
            target_node="n_001",
            relation_type="manifests_as",
            confidence=0.8,
            mechanism_description="高房价是不确定性的具体表现",
        )
        assert edge.relation_type == "manifests_as"

    def test_self_loop_rejected(self):
        with pytest.raises(ValidationError, match="self-loop"):
            RelationEdge(
                id="e_002",
                source_node="n_001",
                target_node="n_001",  # same
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )

    def test_empty_mechanism_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_003",
                source_node="n_001",
                target_node="n_002",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="",  # 空
            )

    def test_invalid_relation_type_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_004",
                source_node="n_001",
                target_node="n_002",
                relation_type="wrong",  # type: ignore[arg-type]
                confidence=0.5,
                mechanism_description="x",
            )

    def test_all_relation_types_accepted(self):
        types: list[RelationType] = [
            "causes",
            "amplifies",
            "suppresses",
            "constrains",
            "manifests_as",
        ]
        for t in types:
            edge = RelationEdge(
                id=f"e_{t}",
                source_node="n_001",
                target_node="n_002",
                relation_type=t,
                confidence=0.5,
                mechanism_description="x",
            )
            assert edge.relation_type == t

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="",
                source_node="n_001",
                target_node="n_002",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )

    def test_empty_source_target_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_001",
                source_node="",
                target_node="n_002",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_001",
                source_node="n_001",
                target_node="",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )

    def test_confidence_boundary_values_accepted(self):
        for c in [0.0, 1.0]:
            edge = RelationEdge(
                id=f"e_{c}",
                source_node="n_001",
                target_node="n_002",
                relation_type="causes",
                confidence=c,
                mechanism_description="x",
            )
            assert edge.confidence == c
