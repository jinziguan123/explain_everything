"""Phase 15: chat_copy 中文文案 + 术语词典."""


class TestTermsMap:
    def test_critical_terms_present(self):
        """关键术语必须有映射 (L0/L1/L2 / stage 4 值 / graph / lexicon)."""
        from explain_engine.chat.chat_copy import TERMS_MAP
        required = {
            "L0", "L1", "L2",
            "bootstrap_pending", "insight_pending", "done", "converged",
            "graph", "edge", "lexicon",
        }
        assert required.issubset(set(TERMS_MAP.keys()))

    def test_l_levels_mapped_to_chinese_phrases(self):
        """L0/L1/L2 应映射成直观短语, 不含 'L'."""
        from explain_engine.chat.chat_copy import TERMS_MAP
        assert "L" not in TERMS_MAP["L0"]
        assert "L" not in TERMS_MAP["L1"]
        assert "L" not in TERMS_MAP["L2"]


class TestZhHelper:
    def test_zh_translates_known_term(self):
        from explain_engine.chat.chat_copy import zh
        assert zh("L0") == "现象"
        assert zh("bootstrap_pending") == "等待启动"

    def test_zh_fallback_returns_original(self):
        from explain_engine.chat.chat_copy import zh
        assert zh("nonexistent_term") == "nonexistent_term"
