"""Wave 4 Task 4.1: VariableNode lifecycle 字段 backward compat tests."""

from explain_engine.schema.nodes import VariableNode


class TestLifecycleFieldDefaults:
    def test_old_node_creation_works_without_lifecycle_fields(self) -> None:
        """Pydantic Field defaults → 老代码不传 lifecycle 字段也能 create."""
        node = VariableNode(
            id="c_001", name="n", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        )
        assert node.activation == 1.0
        assert node.stability == 0.0
        assert node.last_used_tick == 0
        assert node.age_ticks == 0
        assert node.lifecycle_state == "active"

    def test_new_node_creation_with_lifecycle_fields(self) -> None:
        node = VariableNode(
            id="c_001", name="n", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
            activation=0.5, stability=0.3, last_used_tick=10,
            age_ticks=20, lifecycle_state="stale",
        )
        assert node.activation == 0.5
        assert node.lifecycle_state == "stale"

    def test_old_session_json_loads_with_defaults(self) -> None:
        """模拟老 session JSON (无 lifecycle 字段) loads 不报错."""
        old_dict = {
            "id": "c_001", "name": "n", "description": "d",
            "abstraction_level": 1, "confidence": 0.7,
            "epistemic": "insight", "evidence_ids": [], "source": "llm",
        }
        node = VariableNode.model_validate(old_dict)
        assert node.lifecycle_state == "active"
        assert node.activation == 1.0
