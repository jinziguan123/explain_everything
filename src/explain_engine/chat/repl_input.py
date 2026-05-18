"""chat REPL input infrastructure (2026-05-18 prompt_toolkit upgrade).

Wave 1: BufferedLogHandler — capped in-memory log handler 替代 stdout
StreamHandler 在 chat REPL 模式期间 (避免 log 撞 prompt_toolkit prompt).

Wave 2+ 会加 SlashCompleter / PromptSession factory / read_input().

设计参考 docs/plans/2026-05-18-chat-repl-prompt-toolkit-design.md.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable


class BufferedLogHandler(logging.Handler):
    """Cap-bounded in-memory log handler for chat REPL mode.

    chat REPL 模式期间替代 stdout StreamHandler — 把 logger.info /
    logger.warning 输出收集到 in-memory deque, 不直接写 stdout (会撞
    prompt_toolkit prompt). ctrl+o popup 时 dump buffer 给用户看.

    capacity: 200 line default. 老 line 自动 evict (deque maxlen).
    listeners: 每次 emit 通知, 用于 prompt_toolkit Buffer refresh.

    Thread-safety: chat REPL 单 asyncio loop, 不需 lock. logger.Handler 基类
    内 lock 已覆盖 emit 路径 (Python logging 默认).
    """

    def __init__(self, capacity: int = 200) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)
        self._listeners: list[Callable[[], None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
            for cb in self._listeners:
                try:
                    cb()
                except Exception:
                    pass  # listener bug 不该撕裂 logging chain
        except Exception:
            self.handleError(record)

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def get_text(self) -> str:
        return "\n".join(self.buffer)
