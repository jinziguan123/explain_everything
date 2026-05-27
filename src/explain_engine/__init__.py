"""explain_engine — 认知 engine package."""

# Phase 19 Wave 7 hotfix: force disable textual kitty CSI-u keyboard protocol
# BEFORE any textual import.
#
# 多 terminal 兼容性 > kitty enhanced features (Phase 19 用 Ctrl+O / Ctrl+L /
# Ctrl+C 都是 ASCII control codes, 不需 enhanced). 此 env 必须在
# `textual.constants` module load 之前 set — `DISABLE_KITTY_KEY` 是
# module-level `Final[bool]`, 一次 evaluate 后即冻结. 放包 root `__init__.py`
# 顶部保证任何 `import explain_engine.*` 必先经过这里.
#
# 之前的 fix 放在 `chat/repl_entry.py::enter_repl_async()` 函数体顶端, 但
# 函数 import 链上层 (e.g. test fixture / cli option parse) 已 trigger textual
# transitive import → env 设得太晚. 移到包 root 是唯一稳路径.
#
# 影响范围: macOS Terminal.app / iTerm2 / WezTerm / kitty / 远端 SSH / IME
# 输入法 …… 所有终端都走 legacy ASCII keyboard 模式 (key event 仍工作, 只是
# 不带 modifier release event 等 kitty 扩展信息).
#
# User override 仍生效: `setdefault` 不覆盖已设的 env, 故 user 显式
# `TEXTUAL_DISABLE_KITTY_KEY=0` 仍可 enable (e.g. 在原生 kitty 终端). 默走
# disable.
import os as _os

_os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")
del _os
