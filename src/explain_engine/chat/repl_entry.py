"""Phase 11 Wave 1 Task 1.B: enter_repl_async — explain 默认 entry, ephemeral REPL.

无参数 `explain` 走 cli.py @app.callback() → enter_repl_async():
1. Build EphemeralChatSession + input_provider (注入 prompt_toolkit read_input)
2. while loop 读 input:
   - slash → dispatch_slash (ephemeral 时大多失败 — Wave 3/4 修)
   - 自然语言 + ephemeral → promote_to_persistent → real ChatSession (切模式)
   - 自然语言 + real chat → handle_user_input (Phase 9 query_loop)
3. Quit / EOF / Ctrl-C → 退出, restore log handler

设计: docs/plans/2026-05-18-phase11-repl-unification-design.md §3.1
"""

from __future__ import annotations

import logging

from rich.console import Console

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.repl_input import (
    BufferedLogHandler,
    make_session,
    read_input,
)
from explain_engine.chat.session import ChatSession
from explain_engine.config import make_llm_client
from explain_engine.persistence.storage_v2 import StorageV2


async def enter_repl_async() -> None:
    """Ephemeral REPL outer loop. 用户进入即是 ephemeral, 首句 promote.

    chat 模式期间 swap root logger 到 BufferedLogHandler (ctrl+o popup 看 log,
    避撞 prompt_toolkit prompt). 退出时 restore 原 handler.
    """
    # 本地 import _render_event 避 cli ↔ repl_entry 循环依赖
    # (cli.py 在 callback 内 lazy import enter_repl_async)
    from explain_engine.cli import _render_event

    console = Console()
    storage = StorageV2()

    # make_llm_client 抛 KeyError 若 env 没设. REPL 入口允许 no-LLM 模式
    # (slash 命令可工作, 自然语言 promote 会失败 — caller 看 LLM error).
    try:
        llm = make_llm_client()
    except KeyError as exc:
        console.print(f"[yellow]LLM 未配置 ({exc}); slash 命令仍可用.[/yellow]")
        llm = None

    log_handler = BufferedLogHandler(capacity=200)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers = [log_handler]
    root_logger.setLevel(logging.INFO)

    try:
        pt_session = make_session(log_handler)

        async def _input_provider(prompt_text: str) -> str:
            return await read_input(pt_session, prompt_text=prompt_text)

        chat: EphemeralChatSession | ChatSession = EphemeralChatSession(
            storage=storage,
            input_provider=_input_provider,
        )

        def _print_banner() -> None:
            """启动 banner — /new (slash_reset_to_ephemeral) 也用同一字符串
            保证视觉一致性. 改这里同时影响启动 + reset 两处."""
            console.print(
                "[bold green]Explain REPL[/bold green] — ephemeral session. "
                "输入问题创建持久 session, /help 看 slash, /quit 退出, "
                "ctrl+o 看 log buffer."
            )

        _print_banner()

        while True:
            try:
                text = await read_input(pt_session)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]退出.[/dim]")
                break

            text = text.strip()
            if not text:
                continue

            # ── slash 路径 ──
            if text.startswith("/"):
                from explain_engine.chat.slash_commands import dispatch_slash

                try:
                    events = await dispatch_slash(chat, text)
                except Exception as exc:
                    console.print(
                        f"[red]slash 失败: {type(exc).__name__}: {exc}[/red]"
                    )
                    continue
                quit_requested = False
                for ev in events:
                    if ev.type == "slash_switch_session":
                        # /resume 切到 real ChatSession
                        new_sid = ev.content["sid"]
                        try:
                            chat = ChatSession(new_sid, llm=llm)
                            chat.input_provider = _input_provider
                            console.print(
                                f"[green]切换至 session {new_sid}.[/green]"
                            )
                            # Phase 16.2.1 hotfix: 切 session 也显 history 段
                            from explain_engine.chat.history_render import (
                                print_history_section,
                            )
                            print_history_section(console, chat)
                        except Exception as exc:
                            console.print(
                                f"[red]切换失败: {type(exc).__name__}: {exc}[/red]"
                            )
                        continue
                    if ev.type == "slash_reset_to_ephemeral":
                        # /new: 清屏 + aclose 当前 + 回 ephemeral. 视觉+逻辑都
                        # 跟 uv run explain 刚跑完一致.
                        if isinstance(chat, ChatSession):
                            try:
                                await chat.aclose()
                            except Exception as exc:
                                console.print(
                                    f"[yellow]aclose 当前 session 失败 "
                                    f"(continuing reset): {exc}[/yellow]"
                                )
                        console.clear()
                        chat = EphemeralChatSession(
                            storage=storage,
                            input_provider=_input_provider,
                        )
                        _print_banner()
                        continue
                    _render_event(console, ev)
                    if ev.type == "slash_quit":
                        quit_requested = True
                if quit_requested:
                    break
                continue

            # ── 自然语言路径 ──
            # ephemeral 时: promote_to_persistent (建 real session)
            if isinstance(chat, EphemeralChatSession):
                if llm is None:
                    console.print(
                        "[red]LLM 未配置, 无法 bootstrap. 设 LLM_* env 后重启.[/red]"
                    )
                    continue
                try:
                    chat = await chat.promote_to_persistent(text, llm)
                except Exception as exc:
                    console.print(
                        f"[red]建 session 失败: {type(exc).__name__}: {exc}[/red]"
                    )
                    continue
                console.print(
                    f"[green]Session {chat.sid} 已创建, 进入 chat 模式.[/green]"
                )
                continue

            # real chat 时: query_loop
            # Wave 1 review I-4 fold: slash_switch_session 后 break 出 async for,
            # 让外 while 下次 iter 用新 chat. 否则后续 yield 的 event 作用到
            # 新 chat (绑定 mistake).
            #
            # Wave 4 review I-A (Wave 5 fold): 这里用 immediate-break 而非
            # `cli._run_chat_repl_async` 的 post-loop swap (capture switch_to_sid
            # → 退出 async for → 再 swap) pattern. 选择理由: REPL 入口里
            # `handle_user_input` 通常只 yield 一组 slash_switch_session (来自
            # /resume 单 dispatch), 后续 event 极少, immediate-break 更直观.
            # 若未来 handler yield mixed events (switch + 后续 info text),
            # 与 cli 同步改 post-loop swap pattern (forward-proof note).
            quit_requested = False
            try:
                async for ev in chat.handle_user_input(text, llm=llm):
                    if ev.type == "slash_switch_session":
                        new_sid = ev.content["sid"]
                        # Final review I-1: snapshot old_sid 前 aclose, 切换
                        # 失败 (e.g. graph.json corrupt) 时 fallback 重建 old
                        # chat 防 chat 指向 already-closed instance.
                        old_sid = chat.sid
                        try:
                            await chat.aclose()
                            chat = ChatSession(new_sid, llm=llm)
                            chat.input_provider = _input_provider
                            console.print(
                                f"[green]切换至 session {new_sid}.[/green]"
                            )
                            # Phase 16.2.1 hotfix: 切 session 也显 history 段
                            from explain_engine.chat.history_render import (
                                print_history_section,
                            )
                            print_history_section(console, chat)
                        except Exception as exc:
                            console.print(
                                f"[red]切换失败: {type(exc).__name__}: {exc}[/red]"
                            )
                            try:
                                chat = ChatSession(old_sid, llm=llm)
                                chat.input_provider = _input_provider
                                console.print(
                                    f"[dim]恢复至原 session {old_sid}.[/dim]"
                                )
                            except Exception as recover_exc:
                                console.print(
                                    f"[red]恢复原 session ({old_sid}) 也失败: "
                                    f"{recover_exc}. 退出 REPL.[/red]"
                                )
                                return
                        break  # 退出 async for, 老 chat generator 不再继续 yield
                    _render_event(console, ev)
                    if ev.type == "slash_quit":
                        quit_requested = True
            except Exception as exc:
                console.print(
                    f"[red]Error: {type(exc).__name__}: {exc}[/red]"
                )
                continue
            if quit_requested:
                break

        # 真 chat 退出时 aclose (持久化 chat_state + 跑 background tasks)
        if isinstance(chat, ChatSession):
            await chat.aclose()
            console.print(f"[green]Session {chat.sid} saved.[/green]")
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
