"""PromptLoader test."""

import pytest

from explain_engine.llm.prompts._loader import load_prompt


class TestLoadPrompt:
    def test_load_existing_prompt(self):
        prompt = load_prompt("variable_extraction")
        assert "system" in prompt
        assert "user_template" in prompt
        assert isinstance(prompt["system"], str)
        assert isinstance(prompt["user_template"], str)

    def test_user_template_has_placeholders(self):
        prompt = load_prompt("variable_extraction")
        # 必须有 question / min_count / max_count 占位符
        for placeholder in ["{question}", "{min_count}", "{max_count}"]:
            assert placeholder in prompt["user_template"], (
                f"missing placeholder {placeholder} in user_template"
            )

    def test_load_missing_prompt_raises(self):
        with pytest.raises(FileNotFoundError, match="not_a_real_prompt"):
            load_prompt("not_a_real_prompt")
