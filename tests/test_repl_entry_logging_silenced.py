"""Phase 19 Wave 7 hotfix follow-up: chat REPL 启动时静音 root logger 防 textual 渲染污染.

# 真因 (第一性原理)
- `cli.py` module-level 调用 `logging.basicConfig(level=logging.INFO, format="%(message)s")`,
  绑定 root logger StreamHandler.stream = sys.__stderr__ (强引用).
- textual `App.run_async()` 通过 `redirect_stderr` 把 `sys.stderr` 重定向到 capture
  对象, 但 StreamHandler 已 hold 原始引用, `redirect_stderr` **无效隔离**.
- splash on_mount 跑 `_init_lexicon` → `init_lexicon_backend` → `logging.info("lexicon
  backend: PG ✓ ...")` → **直接写 PTY/stderr** 绕过 textual capture, 跟 textual 写
  alt screen 序列**搅在一起**.
- 某些终端 (尤其 macOS Terminal.app) 容忍度低, alt screen 被污染后**整个 textual UI
  渲染不出来**, user 看到 "lexicon backend: PG ✓" 那一行然后**屏幕空白**.

# 验证 user 报告
~/.explain/phase19_debug.log 显示 7 step lifecycle (app_start → splash_push →
init_task_done → hold_sleep_done → splash_pop → banner_mounted → input_focused)
全部跑完, 即 textual 内部 ANSI **完全写出来了**. 用户终端真没渲染 = terminal
渲染失败 = alt buffer 被 stderr 污染.

# 修法
`enter_repl_async` 顶部 (在 textual import 之前) **接管 root logger handlers**:
- 把所有 StreamHandler.stream = sys.__stderr__ 的 handler 重定向到一个 FileHandler
  指向 `$EXPLAIN_HOME/chat.log` (默 ~/.explain/chat.log)
- 静默 stderr 输出, 保留 logging history 在文件 (user 仍可查 debug)
- 用 `setdefault` 路径 best-effort, IO 失败 fallback NullHandler (logging 完全 drop)

# Test 目标
- 调 `enter_repl_async` 后 root logger handlers 不应有写 sys.__stderr__ 的 StreamHandler
- 验证: 所有 root handler 都不写 sys.__stderr__ (走 file 或 null)
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_enter_repl_async_silences_stderr_logging(tmp_path, monkeypatch) -> None:
    """chat REPL 启动后 root logger 不应再有写 sys.__stderr__ 的 handler.

    防 logging.info 污染 textual alt screen buffer 渲染.

    步骤:
    1. mock LLM/init_lexicon/run_async 让 enter_repl_async 跑通 (不真启 textual)
    2. 进 enter_repl_async 之前: cli.py module-level logging.basicConfig 已 set
       root handler.stream = sys.__stderr__
    3. await enter_repl_async() 走 fix path: 静音 stderr-writing handler
    4. 断言: root logger 任何 handler 都不写 sys.__stderr__
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_logging")

    # 触发 cli.py import 以确保 logging.basicConfig 跑过 (绑 sys.__stderr__)
    import explain_engine.cli  # noqa: F401

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_init():
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    async def fake_run_async(self):
        # textual app run_async — 我们 mock 让它瞬返
        pass

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    # ── Precondition: 强制重置 root logger 含 sys.__stderr__ handler ──
    # pytest capsys 可能改 sys.stderr; 这里直接 install 一个 StreamHandler →
    # sys.__stderr__ 模拟 cli.py module-level logging.basicConfig 真实状态.
    root_before = logging.getLogger()
    for h in list(root_before.handlers):
        root_before.removeHandler(h)
    stderr_handler = logging.StreamHandler(sys.__stderr__)
    root_before.addHandler(stderr_handler)
    root_before.setLevel(logging.INFO)

    from explain_engine.chat.repl_entry import enter_repl_async

    try:
        await enter_repl_async(show_splash=False)

        # ── Postcondition: 所有 stderr-writing handler 应已移除 / 重定向 ──
        # 验范围: sys.__stderr__ 原始 PTY 流 (textual capture 拦不住的那个)
        root_after = logging.getLogger()
        stderr_handlers_after = [
            h for h in root_after.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and getattr(h, "stream", None) is sys.__stderr__
        ]
        assert not stderr_handlers_after, (
            f"chat REPL 启动后 root logger 不应再有写 sys.__stderr__ 的 "
            f"StreamHandler (会污染 textual alt screen 渲染). 残留 handlers: "
            f"{stderr_handlers_after}"
        )
    finally:
        # Cleanup: 恢复 root handler 让其他 test 不受影响
        # (test runner 后续 test 也会 import cli.py, basicConfig 是 no-op 当
        # handlers 非空; 我们清空再重设, 让 pytest 后续 test 行为一致.)
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
        logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)


@pytest.mark.asyncio
async def test_enter_repl_async_redirects_logging_to_chat_log_file(
    tmp_path, monkeypatch
) -> None:
    """chat REPL 启动时把 logging 重定向到 ~/.explain/chat.log 文件 (best-effort).

    fix 设计: 不丢 logging history (user 仍能 debug), 只是把 stream 从 stderr
    换成 file. 文件路径走 EXPLAIN_HOME / "chat.log" (跟 phase19_debug.log 同根).

    IO 失败 fallback NullHandler (silent drop) — 这种情况由独立 test 验证.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_logging_file")

    import explain_engine.cli  # noqa: F401

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_init():
        # 模拟 init_lexicon_backend: 跑过, 期间打一行 logging.info
        # 这一行应进 chat.log 文件, 不进 stderr
        logging.info("test fake init: lexicon backend OK")
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    async def fake_run_async(self):
        pass

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    # 强 reset root logger (跟 test 1 同理) — 让 fix 真接管
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.StreamHandler(sys.__stderr__))
    root.setLevel(logging.INFO)

    from explain_engine.chat.repl_entry import enter_repl_async

    try:
        # show_splash=False 路径: repl_entry 自跑 fake_init (会打 logging.info)
        await enter_repl_async(show_splash=False)

        # 验证 chat.log 文件存在且含 fake init 输出
        chat_log = tmp_path / "chat.log"
        assert chat_log.exists(), (
            f"chat REPL 启动应建 {chat_log} 文件接管 logging. "
            f"EXPLAIN_HOME={tmp_path}, 文件列表: {list(tmp_path.iterdir())}"
        )
        content = chat_log.read_text(encoding="utf-8")
        assert "test fake init: lexicon backend OK" in content, (
            f"fake_init 内的 logging.info 应写到 chat.log 文件. "
            f"实际文件内容:\n{content}"
        )
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
        logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
