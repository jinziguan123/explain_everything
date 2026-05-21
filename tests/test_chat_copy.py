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


class TestCommandDescriptions:
    def test_all_19_commands_present(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        expected = {
            "compress", "run", "rescore", "predict", "counterfactual", "cf",
            "show", "graph", "check", "new", "resume", "list", "lexicon",
            "budget", "compact", "save", "migrate", "help", "quit",
        }
        assert expected.issubset(set(COMMAND_DESCRIPTIONS.keys()))

    def test_descriptions_under_50_chars(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        for cmd, desc in COMMAND_DESCRIPTIONS.items():
            assert len(desc) <= 50, f"/{cmd} desc 过长: '{desc}' ({len(desc)} 字)"

    def test_descriptions_contain_chinese(self):
        """每条 desc 含至少 1 中文字 (catch 漏译)."""
        import re
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        chinese_pattern = re.compile(r'[一-鿿]')
        for cmd, desc in COMMAND_DESCRIPTIONS.items():
            assert chinese_pattern.search(desc), f"/{cmd} desc 无中文"


class TestHelpGroups:
    def test_help_groups_have_six_groups(self):
        from explain_engine.chat.chat_copy import HELP_GROUPS_ZH
        assert len(HELP_GROUPS_ZH) == 6

    def test_help_groups_cover_all_19_commands(self):
        from explain_engine.chat.chat_copy import HELP_GROUPS_ZH
        all_cmds_in_groups = set()
        for _, cmds in HELP_GROUPS_ZH:
            all_cmds_in_groups.update(cmds)
        # /cf 是 alias, 在 group 里不重复列
        expected_excl_cf = {
            "compress", "run", "rescore", "predict", "counterfactual",
            "show", "graph", "check", "new", "resume", "list", "lexicon",
            "budget", "compact", "save", "migrate", "help", "quit",
        }
        assert expected_excl_cf.issubset(all_cmds_in_groups)
