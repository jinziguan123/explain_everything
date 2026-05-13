"""compression.yaml 加载测试。"""

import re

from explain_engine.llm.prompts._loader import load_prompt


class TestCompressionPrompt:
    def test_load_compression(self) -> None:
        p = load_prompt("compression")
        assert "system" in p
        assert "user_template" in p

    def test_user_template_placeholders(self) -> None:
        p = load_prompt("compression")
        found = set(re.findall(r"\{(\w+)\}", p["user_template"]))
        expected = {"question", "phenomena_table", "min_count", "max_count"}
        assert found == expected, f"placeholder mismatch: missing={expected - found}, extra={found - expected}"

    def test_system_mentions_compression(self) -> None:
        p = load_prompt("compression")
        # 中文 prompt 中应提到 "压缩" / "abstract" / "候选"
        assert any(w in p["system"] for w in ["压缩", "abstract", "候选", "Compression"])
