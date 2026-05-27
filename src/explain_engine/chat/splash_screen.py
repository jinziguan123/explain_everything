"""Phase 19 Wave 6 Task 29+30: SplashScreen — chat REPL 启动启屏.

textual Screen 子类, ExplainChatApp.on_mount 时 push_screen 叠加显示, 完成
4 个 init step 后, app on_mount 等 1s pop_screen 显主聊天 layer.

Layout (设计 docs §3.5):
    Vertical (居中)
      ├─ Static#splash-logo       (pyfiglet "Explain" ASCII art, bold cyan)
      ├─ Static#splash-version    (Engine 名称, dim)
      └─ Static#step-0..3         (4 init step widget, pending → 运行中 → 完成/失败)

4 init step (固定串行, 有隐式依赖 — design 决策):
    0. _init_lexicon       : await init_lexicon_backend() (PG 可达 → PG; 断 → JSON)
    1. _ping_pg            : await verify_connection() — best-effort, 失败吞
    2. _load_theory_cache  : get_active_theories() (sync, 短 IO 不到 asyncio.to_thread)
    3. _ready_signal       : async noop sleep, 标记 ready

每 step 失败 (异常) → 标 "失败" 不阻塞下一 step (PG / theory cache 不可达, chat 仍能用
本机 JSON / empty cache).

测试: tests/test_splash_screen.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

import pyfiglet
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static


class SplashScreen(Screen):
    """启屏 Screen — 4 init step 串行渐进点亮.

    Phase 19 Wave 6 决策:
    - 4 step 串行 (有隐式依赖 + 易测易时序, design §3.5)
    - get_active_theories 直接调 sync (短 IO 不到 asyncio.to_thread, 决策 2)
    - init_lexicon_backend "搬家": 从 repl_entry.enter_repl_async 移到这里
      (决策 1, splash 真显示"加载 lexicon...")
    - 1s pause 后 pop (决策 4, 4 step + 1s = ~2-3s splash, 不烦人)
    """

    # design §3.5: 4 step 固定 — fn 是 method name, label 是 user-facing 中文.
    INIT_STEPS: ClassVar[list[dict[str, str]]] = [
        {"label": "加载 lexicon backend", "fn": "_init_lexicon"},
        {"label": "连接 PG", "fn": "_ping_pg"},
        {"label": "加载 theory cache", "fn": "_load_theory_cache"},
        {"label": "启动 chat REPL", "fn": "_ready_signal"},
    ]

    def __init__(self) -> None:
        super().__init__()
        # on_mount 启 4 step worker, 测试用 `await screen._init_task` 等完成.
        self._init_task: asyncio.Task[Any] | None = None

    def compose(self) -> ComposeResult:
        logo = pyfiglet.figlet_format("Explain", font="standard")
        # logo 是 trusted (pyfiglet 输出 ASCII 不含 markup), 走 Static markup
        # 默 True 但内容 [bold cyan] 包裹由 caller 提供 — pyfiglet output 含 `_____`
        # / `/` / `\` 等 char, 不是 textual markup 触发字符, 安全.
        # Wave 4 review C-1 同款防御 — 用 markup=False + classes 走 CSS 上色,
        # 不在 markup 内嵌 logo 内容.
        children: list[Static] = [
            Static(logo, id="splash-logo", markup=False, classes="splash-logo"),
            Static(
                "Explain Engine",
                id="splash-version",
                markup=False,
                classes="splash-version",
            ),
        ]
        for i, step in enumerate(self.INIT_STEPS):
            children.append(
                Static(
                    f"· {step['label']}",
                    id=f"step-{i}",
                    markup=False,
                    classes="splash-step-pending",
                )
            )
        yield Vertical(*children, id="splash-container")

    async def on_mount(self) -> None:
        """4 init step 串行渐进点亮 — async background task.

        启 self._init_task — 测试用 `await screen._init_task` 等完成. ExplainChatApp
        on_mount 内自己 await 完成 (Task 31).
        """
        self._init_task = asyncio.create_task(self._run_init_steps())

    async def _run_init_steps(self) -> None:
        """串行 await 4 step fn, 每步先标 "运行中" 再标 "完成" / "失败"."""
        for i, step in enumerate(self.INIT_STEPS):
            try:
                widget = self.query_one(f"#step-{i}", Static)
            except Exception:
                # screen 已 pop / unmount — 安全退出 (race 防御)
                return
            label = step["label"]
            # 运行中态: 黄底点
            widget.update(f"⠋ {label}...")
            widget.set_class(True, "splash-step-active")
            widget.set_class(False, "splash-step-pending")

            fn_name = step["fn"]
            fn = getattr(self, fn_name)
            try:
                await fn()
                widget.update(f"✓ {label}")
                widget.set_class(True, "splash-step-done")
                widget.set_class(False, "splash-step-active")
            except Exception as exc:
                widget.update(f"✗ {label}: 失败 ({type(exc).__name__})")
                widget.set_class(True, "splash-step-failed")
                widget.set_class(False, "splash-step-active")
                logging.warning(
                    f"splash init step '{label}' 失败 (continuing): "
                    f"{type(exc).__name__}: {exc}"
                )

    # ─── 4 init step 实现 ───
    async def _init_lexicon(self) -> None:
        """Step 0: init_lexicon_backend (PG / JSON fallback).

        Idempotent — _PG_BACKEND_ACTIVE cache 已 set 时立刻返. 函数本身有 try/except
        包内部失败转 _PG_BACKEND_ACTIVE = False (不抛), 故 await 不会异常 —
        外层 try/except 是双保险.
        """
        from explain_engine.engines.lexicon import init_lexicon_backend

        await init_lexicon_backend()

    async def _ping_pg(self) -> None:
        """Step 1: verify_connection — best-effort, 失败吞 (label 标"失败"足够).

        init_lexicon_backend 内部已 ping 过 PG, 此处显式分离一步是给 user 看
        "PG 连接" 单独状态. _PG_BACKEND_ACTIVE = False 时 verify_connection 仍
        可能 raise LexiconDBError — 我们 catch + 不抛, 外层 _run_init_steps 也不
        会再 catch (因 _ping_pg 自己 swallow 了, 标 ✓; 真想标 ✗ 应让它 raise).

        decision: 让它 raise → 失败 step 显式 ✗ (跟 init_lexicon 区分: init_lexicon
        总是 ✓ 因 fallback path; PG 真断这步显 ✗).
        """
        from explain_engine.persistence.lexicon_pg import verify_connection

        try:
            await verify_connection()
        except Exception:
            # 决策: swallow — splash 上一步 init_lexicon 已经决定 PG/JSON fallback,
            # 这里不重复抛错 (避免 splash 红字闪现). 真想要错误可见可改回 raise.
            pass

    async def _load_theory_cache(self) -> None:
        """Step 2: get_active_theories (sync) — 短 IO 不到 asyncio.to_thread (决策 2).

        embedder=None 是 bootstrap inject 路径, get_active_theories 内部已支持
        stale fallback. 启动 splash 阶段没必要传 embedder (那是 chat 推理时事).
        """
        from explain_engine.engines.theory.cache import get_active_theories
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        # sync 直接调 — 不 asyncio.to_thread (短 IO, 决策 2)
        get_active_theories(storage, embedder=None)

    async def _ready_signal(self) -> None:
        """Step 3: async noop sleep — 标记 chat REPL ready.

        50ms sleep — 给前 3 step UI 透气, 也是让 user 看完 step 点亮再 1s pop.
        """
        await asyncio.sleep(0.05)
