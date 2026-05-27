"""Phase 19 Wave 3 Task 17 + Wave 6 Task 32: `enter_repl_async` textual TUI.

老 Phase 11 + Phase 18 累积版 (270 行 Rich Console + prompt_toolkit + outer
while loop + chat var 切换) 整段删 — git history 可查 commit `bda0326` 前的
版本.

Phase 19 Wave 6 Task 32: init_lexicon_backend "搬家" 到 SplashScreen._init_lexicon
(决策 1, splash 真显示"加载 lexicon..."). 仅 `show_splash=False` 路径 (cli
`--no-splash`) repl_entry 自己跑 init.

Phase 19 Wave 7 hotfix Bug 2: macOS Terminal.app 不支持 kitty CSI-u 增强键盘协议.
textual 默 enable 会导致用户输汉字 IME 提交时显 raw `[49;;{codepoint}u` escape
而不是真字符. textual.constants.DISABLE_KITTY_KEY 在 module-level 读 env var
`TEXTUAL_DISABLE_KITTY_KEY` (Final, evaluate 一次), 故必须在 `import textual.*`
**之前** set. _disable_kitty_keys_if_apple_terminal() 在 enter_repl_async 函数体
最顶端 (在 lazy import textual app 之前) 调, 跟 textual 设计约束一致.

新 enter_repl_async 流程:
1. macOS Terminal.app detect → setdefault TEXTUAL_DISABLE_KITTY_KEY=1 (Bug 2 fix).
2. make_llm_client / make_light_llm_client (LLM 未配 → None, slash 仍可用).
3. show_splash=False → 自跑 init_lexicon_backend (best-effort);
   show_splash=True → 跳, splash 内会跑.
4. 建 EphemeralChatSession (启始 chat var).
5. 建 ExplainChatApp(llm, light_llm, ephemeral_chat, show_splash=...).
6. await app.run_async() — textual outer loop, exit() / Ctrl+C 退出.

outer loop / chat var 切换 (ephemeral ↔ ChatSession) / slash dispatch 移进
tui_app.py: Input.Submitted handler 调 dispatch_slash, _render_event 接
slash_deepen_promoted / slash_reset_to_ephemeral / slash_quit 改 self.chat
或退出.

设计: docs/plans/2026-05-27-phase-19-tui-design.md §3.3 + §3.5.
"""

from __future__ import annotations

import os


def _disable_kitty_keys_if_apple_terminal() -> None:
    """Wave 7 hotfix Bug 2: macOS Terminal.app detect → 关 textual kitty CSI-u.

    macOS Terminal.app 不支持 kitty 增强键盘协议 (CSI-u escape sequence). textual
    8.x 默 enable, 用户输汉字 IME commit 时终端把汉字 codepoint 走 kitty 编码
    `[49;;{cp1}:{cp2}u` 发回 textual, 但 macOS Terminal.app 自己不识别这些 escape
    → 终端直接把 raw escape 字符显在屏上 (bug 2 production smoke 实证).

    textual.constants 第 116 行:
        DISABLE_KITTY_KEY: Final[bool] = _get_environ_bool("TEXTUAL_DISABLE_KITTY_KEY")
    是 `Final`, 在 `from textual import constants` 时一次性 evaluate. 故 fix 必须
    在 textual import **之前** set env var — 本函数在 enter_repl_async 最顶端
    (lazy import textual app 之前) 调.

    setdefault 而非 [] 赋值: user 显式 set TEXTUAL_DISABLE_KITTY_KEY=0 时不覆盖
    (explicit > convention). 非 Apple_Terminal (iTerm2 / kitty / WezTerm) 跳过,
    保留 textual 默 enhanced 模式 (更精准 key event).
    """
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")


async def enter_repl_async(show_splash: bool = True) -> None:
    """Phase 19 重写: textual TUI chat REPL 入口.

    Args:
        show_splash: True 时 ExplainChatApp.on_mount push SplashScreen, splash
            内跑 init_lexicon_backend (决策 1, 搬家). False 时跳过 splash;
            repl_entry 自跑 init_lexicon_backend (干净路径, 给 CI / 测试用).

    流程:
    1. make_llm_client / make_light_llm_client (KeyError → None).
    2. show_splash=False → 跑 init_lexicon_backend; True → 跳 (splash 跑).
    3. EphemeralChatSession(storage, llm) 启始 chat.
    4. ExplainChatApp(llm, light_llm, ephemeral_chat, show_splash=...).run_async() 启 TUI.

    textual app 内部:
    - Input.Submitted → dispatch_slash / chat.handle_user_input.
    - slash_deepen_promoted → app.chat 切到 ChatSession (用 metadata.sid).
    - slash_reset_to_ephemeral → app.chat 重建 EphemeralChatSession.
    - slash_quit / Ctrl+C → app.exit() 退出.
    """
    # 0. Wave 7 hotfix Bug 2: 必在 import textual.* **之前** detect TERM_PROGRAM →
    # set TEXTUAL_DISABLE_KITTY_KEY. textual.constants.DISABLE_KITTY_KEY 是
    # module-level Final, 一次性 evaluate, 故 import 前 set 是唯一稳路径.
    _disable_kitty_keys_if_apple_terminal()

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
    from explain_engine.chat.tui_app import ExplainChatApp

    app = ExplainChatApp(
        llm=llm,
        light_llm=light_llm,
        ephemeral_chat=ephemeral,
        show_splash=show_splash,
    )
    await app.run_async()
