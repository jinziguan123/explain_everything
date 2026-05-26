"""ephemeral_chat.yaml load test — Phase 18 Task 1."""
from explain_engine.llm.prompts._loader import load_prompt


def test_ephemeral_chat_yaml_loads():
    p = load_prompt("ephemeral_chat")
    assert "system" in p
    assert "deepen" in p["system"].lower()  # 提到 /deepen 引导
    assert "ephemeral" in p["system"].lower() or "system-1" in p["system"]
