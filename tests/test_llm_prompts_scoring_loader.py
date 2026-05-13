"""scoring.yaml 加载测试。"""

import re

from explain_engine.llm.prompts._loader import load_prompt


class TestScoringPrompt:
    def test_load_scoring(self) -> None:
        p = load_prompt("scoring")
        assert "system" in p
        assert "user_template" in p

    def test_user_template_placeholders(self) -> None:
        p = load_prompt("scoring")
        found = set(re.findall(r"\{(\w+)\}", p["user_template"]))
        expected = {"abstract_name", "abstract_description", "concrete_name", "concrete_description", "mechanism"}
        assert found == expected, f"placeholder mismatch: missing={expected - found}, extra={found - expected}"

    def test_system_mentions_scoring(self) -> None:
        p = load_prompt("scoring")
        assert any(w in p["system"] for w in ["评分", "score", "plausibility", "1-5"])
