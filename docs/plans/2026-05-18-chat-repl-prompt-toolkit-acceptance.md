# chat REPL prompt_toolkit 升级 Acceptance Checklist

> Design: [2026-05-18-chat-repl-prompt-toolkit-design.md](2026-05-18-chat-repl-prompt-toolkit-design.md)
> Plan: [2026-05-18-chat-repl-prompt-toolkit-plan.md](2026-05-18-chat-repl-prompt-toolkit-plan.md)

prompt_toolkit Application 真实交互需 tty, 自动测难做. 这 8 步手测覆盖 3 个用户报的 UX issue.

## Setup

1. 确认 HEAD = Wave 4 commit (`0522618`) 或之后
2. 跑 `.venv/bin/python -m pytest -x` 应全 676 PASS
3. 准备 session: 跑 `.venv/bin/python -m explain_engine list` 看现成 sid, 或 `.venv/bin/python -m explain_engine new "smoke test 问题"` 建一个
4. 确保 `.env` 含 LLM 配置 (LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)

## Smoke Steps

### S1: `/` 弹自动联想菜单 (#3)

**操作**: 进 chat:
```bash
.venv/bin/python -m explain_engine chat <sid>
```
启动后输 `/` (不按 enter, 仅一个字符).

**预期**: 终端弹出下拉菜单列 8 个 slash command (quit/help/show/budget/compact/save/new/resume), 每项右侧是 description (灰底白字, 当前选中项稍深).

**失败模式**: 没有菜单弹出 / 菜单空 / 报错.

---

### S2: `/r` 过滤到 resume (#3)

**操作**: 在 S1 菜单基础上继续输 `r` (即输 `/r`).

**预期**: 菜单收窄到只显示 `resume` 一项.

**失败模式**: 菜单不过滤 / 仍显示全 8 项.

---

### S3: `/new 为什么 X` 不再联想 (#3)

**操作**: 按 ESC 清菜单 (或重新输). 输 `/new 为什么 X` (含空格).

**预期**: 菜单消失. 因为 cursor 已不在第一 token, SlashCompleter 早退不联想.

**失败模式**: 菜单仍弹 / 联想自然语言.

---

### S4: LLM 调用期间 log 不撞 prompt (#1)

**操作**: ESC 清. 输自然语言 e.g. `帮我看看 graph 哪里需要 expand`. Enter.
期间 LLM 调用 + tool dispatch + 可能 session_memory_writer 触发.

**预期**:
- 编辑行 `> ` 始终独立, log 不"覆盖"在 user 输入字符上
- chat 响应 (Rich console.print 渲染) 出现在 prompt 上方滚动区
- LLM HTTP log (httpx INFO) 和 session_memory_writer INFO 默认不可见 (走 BufferedLogHandler 了)

**已知 Wave 4 risk** (subagent flagged): Rich `Console()` 在 cli.py module level instantiated, 可能 grab `sys.stdout` 早于 `patch_stdout()`. 如果发现 Rich console.print 没被 patched (即仍撞 prompt), 需 Wave 6 fix (改 Console 用 `file=sys.stdout` lazy or 用 prompt_toolkit print_formatted_text).

**失败模式**: 编辑行被 log 覆盖 / Rich 输出错乱.

---

### S5: 删除中文字符无残影 (#1, regression from Phase 9 readline)

**操作**: 输 `你好` 然后 backspace 删 2 次. 重复几次 (输 + 删).

**预期**: 字符完全消失, 不留视觉残影. (Phase 9 readline 有这个 bug, prompt_toolkit 应 fix.)

**失败模式**: 残留半个字符 / 多字节 cursor 错位 / 删除后行内有 ghost char.

---

### S6: ctrl+o 弹 log popup (#2)

**操作**: 跑过几轮 LLM 调用后 (或先按 enter 几次空), 按 `ctrl+o`.

**预期**:
- 弹出 modal full-screen `message_dialog` 显示 log buffer 内容 (HTTP request log + session_memory_writer log)
- 灰色样式 (#888888 fg) 应用
- 任意 Enter 关闭回 prompt 编辑行

**失败模式**:
- 不弹 dialog (ctrl+o 默认 emacs newline-and-stay 没被 override)
- dialog crash / hang (message_dialog.run_async 异常)
- 关闭后 prompt 错乱

---

### S7: bottom toolbar 计数 (#2)

**操作**: 看 prompt 底部.

**预期**: 一行 toolbar `ctrl+o: log (N lines buffered) | ctrl+d: exit`. N 随 log 增长 (跑过 LLM 调用后非 0).

**失败模式**: toolbar 不显示 / N 始终 0.

---

### S8: 退出后 stdout log 恢复 (#2 副效益)

**操作**: `/quit` 或 ctrl+d 退出 chat. 跑:
```bash
.venv/bin/python -m explain_engine list
```

**预期**: list 命令的 logger.info 正常打 stdout (cli.py:46 `logging.basicConfig` 生效). 不再静默.

**失败模式**: 退出 chat 后 stdout log 仍走 BufferedLogHandler (handler 没 restore — `outer try/finally` bug).

---

## Pass/Fail 标准

8 步全过 → ✅ 接受
任一不过 → 提 issue 含具体 step + 预期 vs 实际

## 已知 trade-off (design 选择)

- ctrl+o 覆盖 prompt_toolkit emacs default newline-and-stay. chat 单行 input 不冲突 (要 multi-line 用 alt+enter).
- log popup 是 modal full-screen (而非 inline floating window) — design §4.4 决定走 simpler 路径.
- history 是 InMemoryHistory (不持久化跨 session) — YAGNI; 跨 turn / 跨 slash 共享 OK.
- `_REPL_STYLE` 是 module-level singleton (Wave 3 reviewer S-2 已 accept).

## Wave 4 Risk Reference (Wave 5 撞了再 fix)

Wave 4 subagent + reviewer 都 flag 2 个 unresolved risk:

### Risk A: Rich Console + patch_stdout 时序

`cli.py:55` `console = Console()` 在 module-level 实例化. Rich `Console.file` default 在每次 print 时取 `sys.stdout` (动态). 但**理论上**应该被 `patch_stdout()` ctx-manager 替换 — Wave 4 reviewer 独立 verify 了 Rich Console 是 lazy attribute 拿. 实际 acceptance 需 confirm.

如果 S4 失败 (Rich 输出撞 prompt), Wave 6 follow-up:
- 改 `console = Console()` 用 `file=sys.stdout` 显式延迟
- 或 chat 模式期间用 prompt_toolkit `print_formatted_text` 替代 console.print
- 或 patch_stdout 加 `raw=False, redraw=True` 看是否解

### Risk B: ChatSession 内部 console.print 行为

`chat/loop.py` + `chat/session.py` + handler 都用 Rich console.print. 同 Risk A.

## Pass 后

- 把本文件状态从 "draft" 改为 "passed" (顶头加一行)
- final code review + finishing-a-development-branch
- 如果新发现 issue, 加 Wave 6 fix 计划

## 参考

- Wave 1-4 commits: `e064732` / `ad142bf` / `5f6f310` / `0522618`
- prompt_toolkit docs: https://python-prompt-toolkit.readthedocs.io/
- Rich Console docs: https://rich.readthedocs.io/en/stable/console.html
