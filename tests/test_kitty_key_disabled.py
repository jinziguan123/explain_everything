"""Phase 19 Wave 7 hotfix regression test: TEXTUAL_DISABLE_KITTY_KEY 必须在
`import explain_engine` 顶层 setdefault, 保证任何 textual import 链 (cli /
repl_entry / tui_app / test) 拉 `textual.constants` 时 module-level Final
`DISABLE_KITTY_KEY` 读到 True.

背景:
- textual 8.x 默 enable kitty CSI-u 增强键盘协议. 不识别 CSI-u 的终端
  (macOS Terminal.app / 某些 iTerm 配置 / WezTerm 旧版 / SSH 转发 / IME) 会把
  raw escape `[49;;{codepoint}u` 当字符显出来 — production bug 2 现象.
- 老 fix (`chat/repl_entry.py::_disable_kitty_keys_if_apple_terminal`) 只对
  `TERM_PROGRAM=Apple_Terminal` set env, 其他终端完全不动 → user 在 iTerm /
  WezTerm 等仍踩 raw escape bug.
- 新 fix: env setdefault 移到 `explain_engine/__init__.py` 顶部 — 任何 import
  explain_engine 必先经过包 root, env 在 textual import 之前就 set. 不再
  detect TERM_PROGRAM (Phase 19 只用 ASCII control codes — Ctrl+O/L/C, 不需
  kitty 增强协议). 跨终端通用.

setdefault 保留 user override: `TEXTUAL_DISABLE_KITTY_KEY=0` 仍可强 enable.

注: 此 test 不能 monkeypatch.delenv 后重新 import explain_engine — 包已 load,
__init__.py 不会重跑. 故验证目标是 "test 进程启动时 (= import explain_engine
后) env 已被 set, 且 textual.constants 真值是 True".
"""

from __future__ import annotations

import os


def test_kitty_key_env_set_at_package_import() -> None:
    """import explain_engine 后, TEXTUAL_DISABLE_KITTY_KEY env 必须 == '1'.

    (test 进程已经 import 过 explain_engine — conftest / test collection 必拉.)
    """
    # 包级 __init__.py setdefault 应已经跑过
    import explain_engine  # noqa: F401  确保已 import (test runner 通常已 load)

    val = os.environ.get("TEXTUAL_DISABLE_KITTY_KEY")
    assert val == "1", (
        "Phase 19 Wave 7 hotfix: explain_engine/__init__.py 顶部应 setdefault "
        "TEXTUAL_DISABLE_KITTY_KEY='1'. textual.constants.DISABLE_KITTY_KEY "
        "是 module-level Final, 必须在任何 textual import 之前 set. "
        f"got {val!r}"
    )


def test_textual_constants_disable_kitty_key_true() -> None:
    """真 import textual.constants, DISABLE_KITTY_KEY 必须 == True.

    这是终态验证 — 不只 env 字符串对, textual 自己 evaluate 后的 Final
    bool 值也对. 老 fix 因时序问题 evaluate 出 False, 用户 IME 输汉字仍踩
    raw CSI-u escape bug.
    """
    # 顺序敏感: 必须先 import explain_engine (顶部 setdefault env), 再 import
    # textual.constants. ruff isort I001 默会按字典序排 — 这里用 isort: off
    # 强制按代码序保留 (语义直接冲突).
    # isort: off
    import explain_engine  # noqa: F401 — 必须先 import 触发包级 env set
    import textual.constants

    # isort: on

    assert textual.constants.DISABLE_KITTY_KEY is True, (
        "textual.constants.DISABLE_KITTY_KEY 应在 import explain_engine 后 "
        "evaluate 为 True. 若 False, fix 时序错 — env set 在 textual import "
        "之后, 修法: 把 setdefault 提前到包 root __init__.py."
    )
