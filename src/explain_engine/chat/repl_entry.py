"""Phase 19 Wave 3 Task 17 + Wave 6 Task 32: `enter_repl_async` textual TUI.

老 Phase 11 + Phase 18 累积版 (270 行 Rich Console + prompt_toolkit + outer
while loop + chat var 切换) 整段删 — git history 可查 commit `bda0326` 前的
版本.

Phase 19 Wave 6 Task 32: init_lexicon_backend "搬家" 到 SplashScreen._init_lexicon
(决策 1, splash 真显示"加载 lexicon..."). 仅 `show_splash=False` 路径 (cli
`--no-splash`) repl_entry 自己跑 init.

Phase 19 Wave 7 hotfix Bug 2 + 后续 hotfix: textual 默 enable kitty CSI-u
增强键盘协议导致跨终端兼容性问题 (raw `[49;;{codepoint}u` 显屏). 修法上移
到 `explain_engine/__init__.py` 顶部 setdefault `TEXTUAL_DISABLE_KITTY_KEY=1`
— 保证任何 textual import 之前 env 已就位. 老在本文件 `enter_repl_async`
函数体顶端 detect TERM_PROGRAM 的 helper 已删除 (时序+覆盖范围都不对).

Phase 19 Wave 7 hotfix follow-up: logging stderr 污染 textual alt screen 修.
真因 (第一性原理):
- `cli.py` module-level 调 `logging.basicConfig(level=INFO, format=...)`,
  绑 root logger StreamHandler.stream = sys.__stderr__ (强引用 PTY 流).
- textual `App.run_async()` 通过 `redirect_stderr` 把 `sys.stderr` 重定到
  capture 对象, 但 StreamHandler 已 hold 原始引用, redirect_stderr **无效
  隔离**.
- splash on_mount → `_init_lexicon` → `init_lexicon_backend` → `logging.info(
  "lexicon backend: PG ✓ ...")` → 直接写 PTY 绕过 textual, 跟 textual 写
  alt screen 序列**搅在一起**.
- 某些终端 (尤其 macOS Terminal.app) 容忍度低, alt screen 被污染后 textual
  UI **整个渲染不出来** (user 看到 "lexicon backend: PG ✓" 然后**屏幕空白**).
- 验证: ~/.explain/phase19_debug.log 显示 7 step lifecycle 全跑完 (即 textual
  ANSI 全写出来了), 用户真终端没渲染 = terminal 渲染失败 = alt buffer 被
  stderr 污染.
修法: `enter_repl_async` 顶部 **接管 root logger handlers** —
- 移走所有写 sys.__stderr__ 的 StreamHandler (任何会污染 PTY 的 handler);
- 加一个 FileHandler 指 `$EXPLAIN_HOME/chat.log` (默 ~/.explain/chat.log),
  logging history 仍保留可 debug;
- 文件 IO 失败 fallback NullHandler (silent drop), chat REPL 仍能启.
不动 cli.py module-level basicConfig — 其他 cli 子命令 (`explain list` / `new` 等)
仍需 stderr 输出 progress logger. 仅 textual TUI 模式静音.

Phase 20.2 决策反转: app.run_async(mouse=True) — 重开 SGR mouse tracking 让鼠标
滚轮翻历史. Phase 19 真终端 Bug A 当初设 mouse=False 换终端原生 click-drag 选字
复制, 但用户实际要滚轮回看历史. 终端 mouse tracking all-or-nothing (滚轮 button
64/65 须开 click tracking 才收) → 只能 mouse=True. 代价: click-drag 选字被 app
抢走, 但 iTerm2 / Terminal.app 按 Shift / Option 拖拽仍可原生选字复制.

新 enter_repl_async 流程:
1. **静音 root logger stderr handler** → 重定到 `chat.log` 文件 (防 textual
   渲染污染) — 必须在 textual 任何 import 之前.
2. make_llm_client / make_light_llm_client (LLM 未配 → None, slash 仍可用).
3. show_splash=False → 自跑 init_lexicon_backend (best-effort);
   show_splash=True → 跳, splash 内会跑.
4. 建 EphemeralChatSession (启始 chat var).
5. 建 ExplainChatApp(llm, light_llm, ephemeral_chat, show_splash=...).
6. await app.run_async(mouse=True) — textual outer loop, exit() / Ctrl+C 退出.

outer loop / chat var 切换 (ephemeral ↔ ChatSession) / slash dispatch 移进
tui_app.py: Input.Submitted handler 调 dispatch_slash, _render_event 接
slash_deepen_promoted / slash_reset_to_ephemeral / slash_quit 改 self.chat
或退出.

设计: docs/plans/2026-05-27-phase-19-tui-design.md §3.3 + §3.5.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _silence_stderr_logging_for_textual() -> None:
    """Wave 7 hotfix follow-up: 静音 root logger stderr handler 防 textual 渲染污染.

    真因: cli.py module-level `logging.basicConfig(stream=sys.__stderr__)`
    绑 StreamHandler.stream = sys.__stderr__ (强引用 PTY 流). textual
    `App.run_async()` 后续 `redirect_stderr(_capture_stderr)` 拦不住 ——
    StreamHandler 已 hold 原始引用, 把 logging 输出直写 PTY, 跟 textual
    alt screen ANSI 搅在一起, 在某些终端 (macOS Terminal.app 等容忍度低
    的) **整个 textual UI 渲染失败** (user 看 "lexicon backend: PG ✓"
    然后屏幕空白).

    本函数:
    1. 遍历 root logger handlers, **移除所有 StreamHandler.stream is
       sys.__stderr__** (这是 PTY 流, textual capture 拦不住的那个).
    2. 加一个 FileHandler 指 `$EXPLAIN_HOME/chat.log` (默 ~/.explain/chat.log).
       logging history 保留, user 可查 chat.log 看 lexicon init / chat
       内部 logging 输出 debug.
    3. FileHandler IO 失败 (e.g. EXPLAIN_HOME 不可写) → fallback NullHandler
       (silent drop 所有 logging), chat REPL 仍能启 (best-effort).

    必须在 textual import / startup 之前调 — textual 一旦启 alt screen,
    任何残留写 stderr 的 handler 都会污染.

    Idempotent: 多次调安全 (移除已处理 handler 不重复, 加 chat.log handler
    用 baseFilename 去重).
    """
    root = logging.getLogger()

    # 1. 移除所有写 sys.__stderr__ 的 StreamHandler.
    #    注: FileHandler 是 StreamHandler 子类但 stream 是 file object,
    #    不是 sys.__stderr__, 不会被误删.
    to_remove = [
        h for h in list(root.handlers)
        if isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) is sys.__stderr__
    ]
    for h in to_remove:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        root.removeHandler(h)

    # 2. 加 FileHandler → $EXPLAIN_HOME/chat.log (默 ~/.explain/chat.log)
    explain_home = os.environ.get("EXPLAIN_HOME") or os.path.expanduser("~/.explain")
    log_path = Path(explain_home) / "chat.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Idempotent: 同路径 handler 已加则不重复加
        already_added = any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", None) == str(log_path)
            for h in root.handlers
        )
        if not already_added:
            file_handler = logging.FileHandler(
                str(log_path), mode="a", encoding="utf-8"
            )
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            root.addHandler(file_handler)
        # 保持 INFO 级别 (跟 cli.py basicConfig 一致)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
    except OSError:
        # 文件 IO 失败 (e.g. EXPLAIN_HOME 不可写) → fallback NullHandler:
        # 静默 drop 所有 logging, chat REPL 仍能启 (best-effort).
        # Idempotent: 不重复加 NullHandler.
        if not any(isinstance(h, logging.NullHandler) for h in root.handlers):
            root.addHandler(logging.NullHandler())


async def enter_repl_async(show_splash: bool = True) -> None:
    """Phase 19 重写: textual TUI chat REPL 入口.

    Args:
        show_splash: True 时 ExplainChatApp.on_mount push SplashScreen, splash
            内跑 init_lexicon_backend (决策 1, 搬家). False 时跳过 splash;
            repl_entry 自跑 init_lexicon_backend (干净路径, 给 CI / 测试用).

    流程:
    1. **静音 root logger stderr handler** → 重定到 `chat.log` 文件 (防
       textual 渲染污染 — Wave 7 hotfix follow-up).
    2. make_llm_client / make_light_llm_client (KeyError → None).
    3. show_splash=False → 跑 init_lexicon_backend; True → 跳 (splash 跑).
    4. EphemeralChatSession(storage, llm) 启始 chat.
    5. ExplainChatApp(llm, light_llm, ephemeral_chat, show_splash=...).run_async() 启 TUI.

    textual app 内部:
    - Input.Submitted → dispatch_slash / chat.handle_user_input.
    - slash_deepen_promoted → app.chat 切到 ChatSession (用 metadata.sid).
    - slash_reset_to_ephemeral → app.chat 重建 EphemeralChatSession.
    - slash_quit / Ctrl+C → app.exit() 退出.

    注: textual kitty CSI-u disable 已在 `explain_engine/__init__.py` 顶部
    setdefault, 不再在本函数内 detect TERM_PROGRAM.
    """
    # 0. Wave 7 hotfix follow-up: 静音 root logger stderr handler.
    # 必须在 textual 任何 import / startup 之前 — 一旦 alt screen 启, logging
    # 输出会直写 PTY 污染渲染. 见 module docstring "真因" 段.
    _silence_stderr_logging_for_textual()

    # 1. LLM clients — make_llm_client 抛 KeyError 若 env 未配; slash 仍可工作.
    from explain_engine.config import make_light_llm_client, make_llm_client

    try:
        llm = make_llm_client()
    except KeyError:
        llm = None
    try:
        light_llm = make_light_llm_client()
    except KeyError:
        light_llm = None

    # 2. Lexicon backend init (PG / JSON fallback).
    # Phase 19 Wave 6 Task 32 (搬家决策 1):
    # - show_splash=True (默): SplashScreen._init_lexicon 跑, 这里跳过.
    # - show_splash=False (--no-splash 干净路径): 这里跑.
    if not show_splash:
        from explain_engine.engines.lexicon import init_lexicon_backend

        try:
            await init_lexicon_backend()
        except Exception:
            # backend init 失败 → textual app 仍启 (Phase 17.1 设计本意:
            # PG 不可达 → 本机 JSON; 完全失败仍可用 in-memory).
            pass

    # 3. EphemeralChatSession — chat 启始态.
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)

    # 4. textual App run_async — outer loop, 直到 app.exit().
    # Phase 20.2 决策反转: mouse=True 重开 SGR mouse tracking, 让鼠标滚轮翻历史.
    # Phase 19 真终端 Bug A 当初设 mouse=False, 为的是终端原生 click-drag 选字
    # 复制 (mouse=True 时 textual 把 click/drag 当 input 抢走). 但 Phase 20.2
    # auto-scroll 智能锚定修好"输出到一半"主症状后, 用户实际要鼠标滚轮回看历史.
    # 终端 mouse tracking 是 all-or-nothing — 滚轮事件 (button 64/65) 必须开
    # click tracking 才收得到, 没有"只收滚轮"模式 → 只能 mouse=True.
    # 代价: click-drag 原生选字被 app 抢走, 但 iTerm2 / macOS Terminal.app 按住
    # Shift / Option 拖拽仍可原生选字复制. 显式传 mouse=True (不靠 textual 默认)
    # 让意图清晰 + 防 textual 未来改默认.
    from explain_engine.chat.tui_app import ExplainChatApp

    app = ExplainChatApp(
        llm=llm,
        light_llm=light_llm,
        ephemeral_chat=ephemeral,
        show_splash=show_splash,
    )
    await app.run_async(mouse=True)
