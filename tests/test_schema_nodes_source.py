"""VariableNode.source 字段测试（Phase 4 新加）。"""

import pytest
from pydantic import ValidationError

from explain_engine.schema.nodes import VariableNode


class TestVariableNodeSource:
    def test_default_source_is_llm(self) -> None:
        """不传 source 时 default 'llm'（兼容旧 JSON）。"""
        n = VariableNode(
            id="p_001",
            name="x",
            description="x",
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
        )
        assert n.source == "llm"

    def test_explicit_user_source(self) -> None:
        n = VariableNode(
            id="p_001",
            name="x",
            description="x",
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
            source="user",
        )
        assert n.source == "user"

    def test_invalid_source_raises(self) -> None:
        with pytest.raises(ValidationError):
            VariableNode(
                id="p_001",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
                source="auto",  # type: ignore[arg-type]
            )

    def test_source_round_trip(self) -> None:
        n = VariableNode(
            id="c_001",
            name="x",
            description="x",
            abstraction_level=1,
            confidence=0.7,
            epistemic="insight",
            source="user",
        )
        d = n.model_dump()
        assert d["source"] == "user"
        n2 = VariableNode.model_validate(d)
        assert n2.source == "user"

    def test_legacy_json_without_source(self) -> None:
        """旧 session JSON 无 source 字段，反序列化默认 'llm'。"""
        legacy = {
            "id": "p_001",
            "name": "x",
            "description": "x",
            "abstraction_level": 0,
            "confidence": 0.7,
            "epistemic": "observation",
            "evidence_ids": [],
        }
        n = VariableNode.model_validate(legacy)
        assert n.source == "llm"
