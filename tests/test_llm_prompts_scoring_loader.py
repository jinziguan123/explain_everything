"""scoring.yaml 加载测试。"""

from explain_engine.llm.prompts._loader import load_prompt


class TestScoringPrompt:
    def test_load_scoring(self) -> None:
        p = load_prompt("scoring")
        assert "system" in p
        assert "user_template" in p

    def test_user_template_placeholders(self) -> None:
        p = load_prompt("scoring")
        for ph in [
            "{abstract_name}",
            "{abstract_description}",
            "{concrete_name}",
            "{concrete_description}",
            "{mechanism}",
        ]:
            assert ph in p["user_template"], f"missing {ph}"

    def test_system_mentions_scoring(self) -> None:
        p = load_prompt("scoring")
        assert any(w in p["system"] for w in ["评分", "score", "plausibility", "1-5"])
