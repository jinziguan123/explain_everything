"""compression.yaml 加载测试。"""

from explain_engine.llm.prompts._loader import load_prompt


class TestCompressionPrompt:
    def test_load_compression(self) -> None:
        p = load_prompt("compression")
        assert "system" in p
        assert "user_template" in p

    def test_user_template_placeholders(self) -> None:
        p = load_prompt("compression")
        for ph in ["{question}", "{phenomena_table}", "{min_count}", "{max_count}"]:
            assert ph in p["user_template"], f"missing {ph}"

    def test_system_mentions_compression(self) -> None:
        p = load_prompt("compression")
        # 中文 prompt 中应提到 "压缩" / "abstract" / "候选"
        assert any(w in p["system"] for w in ["压缩", "abstract", "候选", "Compression"])
