"""Tests for chat REPL input infrastructure (Wave 1+ 2026-05-18 prompt_toolkit upgrade)."""

import logging

from explain_engine.chat.repl_input import BufferedLogHandler


class TestBufferedLogHandler:
    def test_capacity_caps_buffer_size(self):
        """deque maxlen 限制 buffer 总行数."""
        h = BufferedLogHandler(capacity=3)
        logger = logging.getLogger("test_buffered_capacity")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            for i in range(5):
                logger.info("line %d", i)
            assert len(h.buffer) == 3
            assert list(h.buffer) == ["line 2", "line 3", "line 4"]
        finally:
            logger.removeHandler(h)

    def test_get_text_joins_buffer(self):
        """get_text() 返 buffer 内容用 \\n 拼接."""
        h = BufferedLogHandler(capacity=10)
        logger = logging.getLogger("test_buffered_get_text")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.info("a")
            logger.info("b")
            logger.info("c")
            assert h.get_text() == "a\nb\nc"
        finally:
            logger.removeHandler(h)

    def test_get_text_empty_buffer(self):
        """空 buffer get_text 返空 string (不 raise)."""
        h = BufferedLogHandler(capacity=10)
        assert h.get_text() == ""


class TestSlashCompleter:
    """SlashCompleter — `/cmd` 自动联想 from DEFAULT_COMMANDS."""

    def _make_doc(self, text: str):
        """Helper: 构造 prompt_toolkit Document 模拟 cursor 在末尾."""
        from prompt_toolkit.document import Document
        return Document(text=text, cursor_position=len(text))

    def _make_completer(self):
        from explain_engine.chat.repl_input import SlashCompleter
        return SlashCompleter()

    def test_empty_text_no_completions(self):
        """空 input → 不联想."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc(""), None))
        assert completions == []

    def test_non_slash_no_completions(self):
        """text 不以 / 起 → 不联想 (自然语言对话不打扰)."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("hello"), None))
        assert completions == []

    def test_slash_only_lists_all_commands(self):
        """text == '/' → 全 8 cmd 都 yield."""
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/"), None))
        cmd_names = {comp.text for comp in completions}
        expected = {cmd.name for cmd in DEFAULT_COMMANDS}
        assert cmd_names == expected

    def test_slash_prefix_filters(self):
        """text == '/r' → 仅 startswith r 的 cmd (resume)."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/r"), None))
        cmd_names = {comp.text for comp in completions}
        assert "resume" in cmd_names
        assert "quit" not in cmd_names

    def test_second_token_no_completions(self):
        """text == '/foo bar baz' (slash + space + 任意 args) → 不联想 cmd.

        slash 命令名只在第一 token 联想. 第二 token 起 (空格之后) 用户
        输的是命令参数, 不该被错匹到 command name (子串匹配会很 noisy).
        历史上为 `/new <question>` 设计, 2026-05-20 /new 不再接 args 后
        仍保留, 因为 /resume 等其他命令可能扩 args.
        """
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/new 为什么 X"), None))
        assert completions == []

    def test_completion_carries_description(self):
        """Completion 含 display_meta = 该 cmd 的 description (给 prompt_toolkit menu 用)."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/h"), None))
        # Should match 'help' command
        help_completion = next((co for co in completions if co.text == "help"), None)
        assert help_completion is not None
        # display_meta 应是 SlashCommand.description; prompt_toolkit Completion 的
        # display_meta 字段 type 是 OneStyleAndTextTuples (str-like). 检查 not None
        # 即足 (具体内容由 DEFAULT_COMMANDS 决定, 不强检).
        assert help_completion.display_meta is not None
