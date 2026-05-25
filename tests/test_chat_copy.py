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


class TestStopReasonMap:
    def test_known_stop_reasons_translated(self):
        from explain_engine.chat.chat_copy import STOP_REASON_MAP
        assert "已收敛" in STOP_REASON_MAP.get("converged", "")
        assert "预算耗尽" in STOP_REASON_MAP.get("budget_exhausted", "")


class TestSuccessMessageTemplates:
    def test_msg_compress_done_contains_n_and_chinese(self):
        from explain_engine.chat.chat_copy import msg_compress_done
        msg = msg_compress_done(n_candidates=5, n_to_lexicon=3)
        assert "5" in msg
        assert "3" in msg
        assert "归纳" in msg or "模式" in msg
        assert "概念库" in msg

    def test_msg_run_done_translates_stop_reason(self):
        from explain_engine.chat.chat_copy import msg_run_done
        msg = msg_run_done(stop_reason="converged", tick=7)
        assert "已收敛" in msg
        assert "converged" not in msg
        assert "7" in msg

    def test_msg_rescore_done_includes_avg_and_count(self):
        from explain_engine.chat.chat_copy import msg_rescore_done
        msg = msg_rescore_done(n_edges=25, avg_conf=0.78)
        assert "25" in msg
        assert "0.78" in msg
        assert "重评" in msg
        assert "可信度" in msg


class TestErrorTemplates:
    def test_err_failed_includes_exc_info(self):
        from explain_engine.chat.chat_copy import err_failed
        msg = err_failed("compress", ValueError("bad input"))
        assert "compress" in msg
        assert "失败" in msg
        assert "ValueError" in msg
        assert "bad input" in msg

    def test_err_no_llm_chinese(self):
        from explain_engine.chat.chat_copy import err_no_llm
        msg = err_no_llm("predict")
        assert "predict" in msg
        assert "LLM" in msg
        assert "没配置" in msg or "启动" in msg

    def test_err_ephemeral_reject_actionable(self):
        from explain_engine.chat.chat_copy import err_ephemeral_reject
        msg = err_ephemeral_reject("compress")
        assert "/resume" in msg or "新建" in msg or "建 session" in msg

    def test_err_stage_not_allowed_translates_stages(self):
        from explain_engine.chat.chat_copy import err_stage_not_allowed
        msg = err_stage_not_allowed("run", "bootstrap_pending", ["done"])
        assert "等待启动" in msg
        assert "已归纳" in msg
        assert "bootstrap_pending" not in msg
        assert "['done']" not in msg


class TestHintsByKey:
    def test_all_six_keys_present(self):
        from explain_engine.chat.chat_copy import HINTS_BY_KEY
        expected = {
            "need_promote_first", "need_compress_first",
            "after_compress", "after_run",
            "after_inference", "after_rescore",
        }
        assert expected.issubset(HINTS_BY_KEY.keys())

    def test_after_compress_mentions_recommended_next_commands(self):
        from explain_engine.chat.chat_copy import HINTS_BY_KEY
        c = HINTS_BY_KEY["after_compress"]
        assert "/run" in c
        assert "/predict" in c
        assert "/counterfactual" in c
        # 关键: 无 reasoning loop / drivers / abstraction jargon
        assert "reasoning loop" not in c
        assert "drivers" not in c
        assert "abstraction" not in c

    def test_need_compress_first_uses_intuitive_chinese(self):
        from explain_engine.chat.chat_copy import HINTS_BY_KEY
        c = HINTS_BY_KEY["need_compress_first"]
        assert "/compress" in c
        assert "归纳" in c or "模式" in c
        assert "abstraction" not in c


class TestStatusAndInfoConstants:
    def test_status_constants_have_rich_markup(self):
        from explain_engine.chat.chat_copy import (
            STATUS_COMPRESS_PROPOSE,
            STATUS_LEXICON_FLUSH,
            STATUS_RUN,
        )
        for s in (STATUS_COMPRESS_PROPOSE, STATUS_RUN, STATUS_LEXICON_FLUSH):
            assert "[bold green]" in s
            assert "[/bold green]" in s
            # 含中文
            import re
            assert re.search(r'[一-鿿]', s)

    def test_info_constants_dim_style(self):
        from explain_engine.chat.chat_copy import (
            INFO_INSIGHT_PENDING_RESUME,
            INFO_MID_STAGE_SAVED,
        )
        for s in (INFO_INSIGHT_PENDING_RESUME, INFO_MID_STAGE_SAVED):
            assert "[dim]" in s
            assert "[/dim]" in s


class TestTheoryCopy:
    def test_command_descriptions_theories(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        assert "theories" in COMMAND_DESCRIPTIONS
        assert "theory" in COMMAND_DESCRIPTIONS
        for k in ("theories", "theory"):
            assert len(COMMAND_DESCRIPTIONS[k]) <= 50
            import re
            assert re.search(r'[一-鿿]', COMMAND_DESCRIPTIONS[k])

    def test_status_theories_compute_markup(self):
        from explain_engine.chat.chat_copy import STATUS_THEORIES_COMPUTE
        assert "[bold green]" in STATUS_THEORIES_COMPUTE
        assert "分析" in STATUS_THEORIES_COMPUTE

    def test_msg_theories_cold_start_numbers(self):
        from explain_engine.chat.chat_copy import msg_theories_cold_start
        m = msg_theories_cold_start(2, 3)
        assert "2" in m and "3" in m and "session" in m

    def test_msg_theory_rejected_id(self):
        from explain_engine.chat.chat_copy import msg_theory_rejected
        assert "t_abc123" in msg_theory_rejected("t_abc123")
        assert "拒绝" in msg_theory_rejected("t_abc123")

    def test_err_theory_not_found_id(self):
        from explain_engine.chat.chat_copy import err_theory_not_found
        m = err_theory_not_found("t_xyz")
        assert "t_xyz" in m
        assert "/theories" in m


class TestHistoryCopy:
    def test_chat_copy_banner_history_constants_exist(self):
        from explain_engine.chat.chat_copy import (
            BANNER_HISTORY_EMPTY,
            BANNER_HISTORY_FOOTER,
            BANNER_HISTORY_HEADER,
        )
        for c in (BANNER_HISTORY_HEADER, BANNER_HISTORY_EMPTY, BANNER_HISTORY_FOOTER):
            assert isinstance(c, str)
            assert len(c) > 0
        assert "{n}" in BANNER_HISTORY_HEADER
        import re
        for c in (BANNER_HISTORY_HEADER, BANNER_HISTORY_EMPTY, BANNER_HISTORY_FOOTER):
            assert re.search(r"[一-鿿]", c)

    def test_chat_copy_history_command_constants_exist(self):
        from explain_engine.chat.chat_copy import (
            HISTORY_FAILED_SUMMARY,
            HISTORY_FOOTER,
            HISTORY_HEADER,
            HISTORY_INTERVENTION_PREFIX,
            HISTORY_SUMMARY_PREFIX,
            HISTORY_TYPE_PREFIX_ASSISTANT,
            HISTORY_TYPE_PREFIX_USER,
        )
        all_consts = (
            HISTORY_HEADER, HISTORY_FOOTER,
            HISTORY_TYPE_PREFIX_USER, HISTORY_TYPE_PREFIX_ASSISTANT,
            HISTORY_INTERVENTION_PREFIX, HISTORY_SUMMARY_PREFIX,
            HISTORY_FAILED_SUMMARY,
        )
        for c in all_consts:
            assert isinstance(c, str)
            assert len(c) > 0
        # placeholders
        assert "{total}" in HISTORY_HEADER and "{shown}" in HISTORY_HEADER
        assert "{error_type}" in HISTORY_FAILED_SUMMARY
        # 中文 (ASSISTANT_PREFIX 是英文 brand "Claude: " 跳过)
        import re
        zh_required = (
            HISTORY_HEADER, HISTORY_FOOTER,
            HISTORY_TYPE_PREFIX_USER,
            HISTORY_INTERVENTION_PREFIX, HISTORY_SUMMARY_PREFIX,
            HISTORY_FAILED_SUMMARY,
        )
        for c in zh_required:
            assert re.search(r"[一-鿿]", c)

    def test_chat_copy_command_descriptions_has_history(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        assert "history" in COMMAND_DESCRIPTIONS
        desc = COMMAND_DESCRIPTIONS["history"]
        assert isinstance(desc, str) and len(desc) <= 50
        import re
        assert re.search(r"[一-鿿]", desc)

    def test_chat_copy_history_err_funcs_callable(self):
        from explain_engine.chat.chat_copy import (
            err_history_limit_range,
            err_history_limit_type,
            err_history_positional,
            err_history_type_invalid,
        )
        # limit_range 含传入值
        m = err_history_limit_range(500)
        assert "500" in m and "200" in m
        # limit_type 含 1-200 提示
        m = err_history_limit_type()
        assert "1-200" in m
        # type_invalid 含传入值 + slash / llm_turn 关键词
        m = err_history_type_invalid("foo")
        assert "foo" in m and "slash" in m and "llm_turn" in m
        # positional 含 --limit / --type 关键词
        m = err_history_positional()
        assert "--limit" in m and "--type" in m
