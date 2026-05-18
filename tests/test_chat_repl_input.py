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

    def test_listener_notified_on_emit(self):
        """每次 emit 调 listener (用于 prompt_toolkit Buffer refresh)."""
        h = BufferedLogHandler(capacity=10)
        calls: list[int] = []
        h.add_listener(lambda: calls.append(1))

        logger = logging.getLogger("test_buffered_listener")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.info("first")
            logger.info("second")
            assert sum(calls) == 2
        finally:
            logger.removeHandler(h)

    def test_listener_exception_does_not_break_emit(self):
        """Listener 抛异常不影响 emit (防 listener bug 死循环)."""
        h = BufferedLogHandler(capacity=10)

        def bad_listener():
            raise RuntimeError("listener bug")

        h.add_listener(bad_listener)
        logger = logging.getLogger("test_buffered_listener_err")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.info("still works")
            assert "still works" in h.buffer
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
