# Phase 16.2: Per-Session REPL History Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给每 session 加 `repl_history.jsonl` sidecar, 持久化 slash 命令 + intervention text + LLM 自然语言对话回合, 仅供 resume banner 展示最近 10 条 + 新 `/history` slash 查询, 不进 LLM context.

**Architecture:** 新 storage 方法对 (`append/load_repl_history`) 跟 `append_transcript` 同模; chat slash dispatcher 加中央 wrapper 自动 snapshot graph 前后 diff 算 delta summary, 21 个 handler 零侵入 (仅 `/predict` `/counterfactual` 各加 1 行 `metadata={"intervention": ...}`); resume banner 拼最近 10 条 (旧→新, 字数截断); 新 `/history` 命令 (默认 30 / 上限 200 / `--type` 多选); 降级第一原则 — history 任何环节失败不影响 chat 主流程.

**Tech Stack:** Python 3.11 + pytest asyncio_mode auto + Rich Console (`force_terminal=False` 渲染 StringIO). `.venv/bin/python -m pytest` (uv venv) + `.venv/bin/ruff check`.

**Design doc:** [docs/plans/2026-05-25-repl-history-persistence-design.md](2026-05-25-repl-history-persistence-design.md) — 读 §4 (Architecture) + §5 (Schema) + §6 (用户接触面) + §7 (Error Handling) + §8 (Testing) 先.

**Commit 规范** (项目惯例, 严格 follow):
- 中文 commit msg, 末尾加 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer
- Format: `<scope> · Phase 16.2 Task N: <action>` (例 `persistence/storage_v2 · Phase 16.2 Task 1.1: ...`)
- 大多数 task 1 commit (red+green 一起); 复杂 task 拆 2 (red commit + green commit)
- 一次只跑 1 task, 全绿才进下一个
- 每 task 完跑 ruff: `.venv/bin/ruff check src/ tests/`
- 1069+ 既有 test 全程不能挂

---

## Wave 1: Storage 层 append/load + 降级

**目标:** `StorageV2.append_repl_history` + `load_repl_history` 工作, jsonl append-only, 损坏行 silent skip + log warn rate-limited.

**依赖:** 无 (zero-dep, 可第一个起跑)

**文件:**
- Modify: `src/explain_engine/persistence/storage_v2.py`
- Test: `tests/test_persistence_storage_v2.py`

**技术参考 — Green 实现代码骨架:**

```python
# src/explain_engine/persistence/storage_v2.py — 类 StorageV2 内追加

import logging

logger = logging.getLogger(__name__)

# ── repl_history.jsonl (append-only, display-only, 不进 LLM context) ──
def append_repl_history(self, sid: str, entry: dict[str, Any]) -> None:
    path = self.session_dir(sid) / "repl_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def load_repl_history(self, sid: str) -> list[dict[str, Any]]:
    path = self.session_dir(sid) / "repl_history.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    warn_count = 0
    total_corrupt = 0
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            total_corrupt += 1
            if warn_count < 5:
                logger.warning(
                    f"repl_history.jsonl line {i+1} corrupt, skip: {e}"
                )
                warn_count += 1
    if total_corrupt > 5:
        logger.warning(
            f"repl_history.jsonl total {total_corrupt} corrupt lines"
        )
    return out
```

**Task 表:**

| Task | Red Test Name (tests/test_persistence_storage_v2.py 末追加) | 关键实现要点 | Commit Message |
|---|---|---|---|
| 1.1 | `test_append_repl_history_creates_file` | 新 sid 第一次 append, 文件存在, jsonl 单行内容跟 entry 一致 | `persistence/storage_v2 · Phase 16.2 Task 1.1: append_repl_history 落地 (空 sid 首次写)` |
| 1.2 | `test_append_repl_history_appends_not_overwrites` | 连 append 3 次, 文件 3 行 (append-only 非覆盖) | `persistence/storage_v2 · Phase 16.2 Task 1.2: append-only 多 entry 顺序保持` |
| 1.3 | `test_append_repl_history_ensure_ascii_false` | 中文 intervention `{"intervention":"假设 JEPA"}` 落盘后含原中文非 `\u` 转义 | `persistence/storage_v2 · Phase 16.2 Task 1.3: ensure_ascii=False 中文落盘` |
| 1.4 | `test_load_repl_history_missing_returns_empty` | 文件不存在返 `[]`, 不抛 | `persistence/storage_v2 · Phase 16.2 Task 1.4: load 缺文件返空 list` |
| 1.5 | `test_load_repl_history_skips_corrupt_lines` | 文件混 3 合法 + 1 坏行, load 返 3 entry, caplog 含 warning | `persistence/storage_v2 · Phase 16.2 Task 1.5: 坏行 silent skip + log warn` |
| 1.6 | `test_load_repl_history_skips_blank_lines` | 空白行 / 仅空格行 skip, 不报 | `persistence/storage_v2 · Phase 16.2 Task 1.6: blank line 忽略` |
| 1.7 | `test_load_repl_history_corrupt_rate_limit_warn` | 100 行全坏, caplog warn 数 6 (5 详细 + 1 summary) | `persistence/storage_v2 · Phase 16.2 Task 1.7: warn rate-limit 5+1 防刷屏` |

**Red Test 代码示例 (Task 1.1):**

```python
# tests/test_persistence_storage_v2.py 末追加

class TestReplHistoryAppend:
    def test_append_repl_history_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2(project_id="testproj")
        entry = {"ts": "2026-05-25T14:00:00+08:00", "type": "slash", "cmd": "compress", "args": [], "summary": "+4 L1"}
        storage.append_repl_history("s_abc", entry)
        path = storage.session_dir("s_abc") / "repl_history.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == entry
```

**Red Test 代码示例 (Task 1.5):**

```python
def test_load_repl_history_skips_corrupt_lines(self, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    storage = StorageV2(project_id="testproj")
    path = storage.session_dir("s_abc") / "repl_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"type":"slash","cmd":"a"}\n'
        'not valid json\n'
        '{"type":"slash","cmd":"b"}\n'
        '{"type":"slash","cmd":"c"}\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        out = storage.load_repl_history("s_abc")
    assert len(out) == 3
    assert [e["cmd"] for e in out] == ["a", "b", "c"]
    assert any("corrupt" in r.message for r in caplog.records)
```

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_persistence_storage_v2.py -v
.venv/bin/ruff check src/explain_engine/persistence/storage_v2.py tests/test_persistence_storage_v2.py
```

---

## Wave 2: Snapshot + Delta 纯函数

**目标:** `_snapshot_graph` / `_snapshot_graph_safe` / `_compute_delta` 3 个纯函数加到 `slash_commands.py`, 输出格式按 design §5.3.

**依赖:** 无 (zero-dep, 跟 Wave 1 可并行)

**文件:**
- Modify: `src/explain_engine/chat/slash_commands.py` (helper 区域, _build_digraph 附近加)
- Test: `tests/test_chat_slash_commands.py`

**技术参考 — Green 实现:**

```python
# src/explain_engine/chat/slash_commands.py — module level helper 加

import logging

_history_logger = logging.getLogger(__name__)

def _snapshot_graph(state) -> dict[str, int]:
    g = state.graph
    return {
        "l0": sum(1 for n in g.nodes.values() if n.abstraction_level == 0),
        "l1": sum(1 for n in g.nodes.values() if n.abstraction_level == 1),
        "l2": sum(1 for n in g.nodes.values() if n.abstraction_level == 2),
        "edges": len(g.edges),
    }

def _snapshot_graph_safe(state) -> dict[str, int] | None:
    try:
        return _snapshot_graph(state)
    except Exception as exc:
        _history_logger.debug(f"snapshot failed: {type(exc).__name__}: {exc}")
        return None

def _compute_delta(before: dict | None, after: dict | None) -> str:
    if before is None or after is None:
        return "(变化未知)"
    parts = []
    if (d := after["l1"] - before["l1"]):  parts.append(f"{d:+d} L1")
    if (d := after["l0"] - before["l0"]):  parts.append(f"{d:+d} 现象")
    if (d := after["l2"] - before["l2"]):  parts.append(f"{d:+d} L2")
    if (d := after["edges"] - before["edges"]):  parts.append(f"{d:+d} 边")
    return " / ".join(parts) if parts else "无变化"
```

**Task 表:**

| Task | Red Test Name (tests/test_chat_slash_commands.py 内新 class `TestSnapshotAndDelta`) | 关键实现 / 测试要点 | Commit Message |
|---|---|---|---|
| 2.1 | `test_snapshot_graph_counts_by_level` | 用 conftest 已有 `make_graph` builder 造 2 L0 + 1 L1 + 0 L2 + 3 edges, 返 `{l0:2, l1:1, l2:0, edges:3}` | `chat/slash · Phase 16.2 Task 2.1: _snapshot_graph 4-count dict` |
| 2.2 | `test_snapshot_graph_empty_graph_returns_zeros` | 空 graph 返全 0 | `chat/slash · Phase 16.2 Task 2.2: snapshot 空图全 0` |
| 2.3 | `test_compute_delta_positive_l1` | before={l0:0,l1:5,l2:0,edges:0} after.l1=6 → `"+1 L1"` | `chat/slash · Phase 16.2 Task 2.3: _compute_delta 正 delta 格式` |
| 2.4 | `test_compute_delta_negative` | l1: 5→3 → `"-2 L1"` (lifecycle decay 算 negative) | `chat/slash · Phase 16.2 Task 2.4: 负 delta 显 -N` |
| 2.5 | `test_compute_delta_zero_omitted` | l1 不变, l0 +5 → 仅 `"+5 现象"` (l1 项不出现) | `chat/slash · Phase 16.2 Task 2.5: 0 delta 字段省略` |
| 2.6 | `test_compute_delta_no_change` | 全相等 → `"无变化"` | `chat/slash · Phase 16.2 Task 2.6: 全无变化 fallback 文案` |
| 2.7 | `test_compute_delta_multi_field_order` | l1=+1, l0=+5, l2=+12, edges=+37 → `"+1 L1 / +5 现象 / +12 L2 / +37 边"` (L1→现象→L2→边 顺序) | `chat/slash · Phase 16.2 Task 2.7: 多字段顺序 L1/现象/L2/边` |
| 2.8 | `test_snapshot_safe_returns_none_on_exception` | mock state.graph 属性抛 → `_snapshot_graph_safe` 返 None, caplog 含 debug | `chat/slash · Phase 16.2 Task 2.8: _snapshot_graph_safe 异常返 None` |
| 2.9 | `test_compute_delta_handles_none_inputs` | before=None → `"(变化未知)"`; after=None → 同 | `chat/slash · Phase 16.2 Task 2.9: delta None 输入降级` |

**Red Test 代码示例 (Task 2.1):**

```python
# tests/test_chat_slash_commands.py 末追加

class TestSnapshotAndDelta:
    def test_snapshot_graph_counts_by_level(self):
        from explain_engine.chat.slash_commands import _snapshot_graph
        from tests.conftest import make_state_with_graph  # 假定 conftest 有 helper
        state = make_state_with_graph(
            nodes=[("p_1", 0), ("p_2", 0), ("c_1", 1)],
            edges=[("p_1", "c_1"), ("p_2", "c_1"), ("c_1", "p_1")],
        )
        snap = _snapshot_graph(state)
        assert snap == {"l0": 2, "l1": 1, "l2": 0, "edges": 3}
```

**注意 conftest fixture:** Wave 2.1 用前先确认 `tests/conftest.py` 有合适 `make_state_with_graph` helper; 若无, 在 conftest 加 fixture (作 Task 2.1 的 step 1.5, 不单独 commit, 跟 Task 2.1 合 1 commit).

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSnapshotAndDelta -v
.venv/bin/ruff check src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py
```

---

## Wave 3: Dispatcher Wrapper

**目标:** `_wrap_handler(name, handler)` decorator 工作 — snapshot before → handler → snapshot after → write entry. 异常时写 error entry 再 raise. KeyboardInterrupt 不写直接 propagate. Storage 写失败 silent.

**依赖:** Wave 1 (storage) + Wave 2 (snapshot/delta)

**文件:**
- Modify: `src/explain_engine/chat/slash_commands.py` (新 helper + registry 重组)
- Test: `tests/test_chat_slash_commands.py`

**技术参考 — Green 实现:**

```python
# src/explain_engine/chat/slash_commands.py

from datetime import datetime, timezone

def _extract_intervention(events) -> str | None:
    """从 list[ChatEvent] 反解 intervention (仅 /predict /counterfactual 设置)."""
    if not events:
        return None
    for evt in events:
        if getattr(evt, "metadata", None) and "intervention" in evt.metadata:
            return evt.metadata["intervention"]
    return None

def _build_history_entry(
    name: str, args: list[str],
    before: dict | None, after: dict | None,
    intervention: str | None,
    error: str | None,
) -> dict:
    ts = datetime.now(timezone.utc).astimezone().isoformat()
    entry: dict = {
        "ts": ts,
        "type": "slash",
        "cmd": name,
        "args": list(args),
        "summary": _compute_delta(before, after) if error is None else f"(执行失败: {error.split(':', 1)[0]})",
    }
    if intervention is not None:
        entry["intervention"] = intervention
    if error is not None:
        entry["error"] = error
    return entry

def _safe_append_history(chat, entry: dict) -> None:
    """Storage 失败不能 propagate."""
    try:
        chat.storage.append_repl_history(chat.sid, entry)
    except Exception as exc:
        _history_logger.warning(
            f"append_repl_history failed for /{entry.get('cmd')}: "
            f"{type(exc).__name__}: {exc}"
        )

def _wrap_handler(name: str, handler):
    """Central wrapper — snapshot+history, handler 零侵入."""
    async def wrapped(chat, args):
        # ephemeral 没 sid / storage, 跳过 history (但仍调 handler)
        if getattr(chat, "is_ephemeral", False) or getattr(chat, "sid", None) is None:
            return await handler(chat, args)

        before = _snapshot_graph_safe(chat.state)
        try:
            result = await handler(chat, args)
        except KeyboardInterrupt:
            raise  # 用户主动放弃, 不写
        except Exception as exc:
            after = _snapshot_graph_safe(chat.state)
            error_repr = f"{type(exc).__name__}: {exc}"
            entry = _build_history_entry(name, args, before, after, None, error_repr)
            _safe_append_history(chat, entry)
            raise

        after = _snapshot_graph_safe(chat.state)
        intervention = _extract_intervention(result)
        entry = _build_history_entry(name, args, before, after, intervention, None)
        _safe_append_history(chat, entry)
        return result

    return wrapped
```

**Registry 改动:** `DEFAULT_COMMANDS = [SlashCommand(name=n, description=d, handler=_wrap_handler(n, h)) for n, d, h in [(...)]]`. 即在 SlashCommand 构造时一次性 wrap 所有 handler.

**Task 表:**

| Task | Red Test Name (`TestWrapHandler` class) | 关键实现 / 测试要点 | Commit Message |
|---|---|---|---|
| 3.1 | `test_wrap_handler_writes_entry_on_success` | fake handler 改 state.graph (+1 L0) 后返 1 ChatEvent; wrapper 调完, `load_repl_history` 返 1 entry, summary 含 `+1 现象` | `chat/slash · Phase 16.2 Task 3.1: _wrap_handler success path 写 entry` |
| 3.2 | `test_wrap_handler_passes_through_args_and_result` | wrapped(chat, ["a","b"]) 跟原 handler 完全等价 (events 同), handler 收到 ["a","b"] 透传 | `chat/slash · Phase 16.2 Task 3.2: 参数 + 返回值透传` |
| 3.3 | `test_wrap_handler_reads_intervention_from_metadata` | fake handler 返 `[ChatEvent(type="x", content="y", metadata={"intervention":"假设 X"})]`, entry 含 `intervention="假设 X"` | `chat/slash · Phase 16.2 Task 3.3: intervention metadata 反解` |
| 3.4 | `test_wrap_handler_no_metadata_no_intervention_key` | fake handler 返 event 无 metadata, entry dict 不含 `intervention` key (KeyError 验证, 而非 None) | `chat/slash · Phase 16.2 Task 3.4: 无 intervention 时字段省略` |
| 3.5 | `test_wrap_handler_handler_exception_writes_error_entry_then_raises` | fake handler 抛 `ValueError("boom")`, wrapper 先写 entry (含 `error="ValueError: boom"`, summary=`"(执行失败: ValueError)"`), 再 raise. pytest.raises 接住 | `chat/slash · Phase 16.2 Task 3.5: handler 异常时先 entry 再 raise` |
| 3.6 | `test_wrap_handler_append_failure_logs_warn_not_raise` | mock `storage.append_repl_history` raise IOError; handler 正常返, wrapper 吞 + caplog warn, 上层不见 IOError | `chat/slash · Phase 16.2 Task 3.6: storage 写失败 silent + log` |
| 3.7 | `test_wrap_handler_snapshot_failure_summary_is_unknown` | mock `_snapshot_graph` 抛, entry.summary == `"(变化未知)"` | `chat/slash · Phase 16.2 Task 3.7: snapshot 失败 summary 降级` |
| 3.8 | `test_wrap_handler_keyboard_interrupt_propagates_no_write` | fake handler 抛 `KeyboardInterrupt`, jsonl 文件不存在 (没写), pytest.raises(KeyboardInterrupt) | `chat/slash · Phase 16.2 Task 3.8: SIGINT 直 propagate 不写` |

**额外 Step (Wave 3 结束 commit):** `DEFAULT_COMMANDS` 注册改 — 把所有 handler 套 `_wrap_handler`. 这是面向所有 21 个 handler 的一次性改动, 测试用既有 `test_chat_slash_commands.py` 全部 19+ test 验证不回归 (改动应零行为变化, 除新增 history 副作用).

| Task | 实现 | Commit |
|---|---|---|
| 3.9 | `DEFAULT_COMMANDS` 列表 SlashCommand 构造改用 `_wrap_handler(name, handler)`. 跑 `pytest tests/test_chat_slash_commands.py -v` 全绿 (既有 ~40+ test 不挂) | `chat/slash · Phase 16.2 Task 3.9: DEFAULT_COMMANDS 全 handler 接 _wrap_handler` |

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v
.venv/bin/ruff check src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py
```

---

## Wave 4: ChatEvent.metadata + 2 Handler Intervention

**目标:** `ChatEvent` dataclass 加 `metadata: dict | None = None`; `_handle_predict` / `_handle_counterfactual` 在 return 时往 event 塞 `metadata={"intervention": text}`.

**依赖:** Wave 3 (wrapper 已经在读 metadata, 现在要让 handler 写)

**文件:**
- Modify: `src/explain_engine/chat/session.py` (ChatEvent dataclass 加字段)
- Modify: `src/explain_engine/chat/slash_commands.py` (2 个 handler return list 时加 metadata)
- Test: `tests/test_chat_session.py` (ChatEvent metadata) + `tests/test_chat_slash_commands.py` (2 handler)

**技术参考 — ChatEvent dataclass 改:**

```python
# src/explain_engine/chat/session.py
from dataclasses import dataclass, field

@dataclass
class ChatEvent:
    type: str
    content: str | dict | None
    metadata: dict | None = None   # 新增
```

**技术参考 — handler 改:**

```python
# _handle_predict 末尾 return 改 (intervention 拿到, prediction 跑完后):
return [ChatEvent(
    type="slash_predict",
    content=(...),
    metadata={"intervention": intervention},  # 新加
)]

# _handle_counterfactual 同模式:
return [ChatEvent(
    type="slash_counterfactual",
    content="\n".join(content_lines),
    metadata={"intervention": intervention},  # 新加
)]
```

注意: 取消 (`return [ChatEvent(type="slash_predict", content="已取消.")]`) 分支 **不加** metadata — design §4.4 / §5.4 已明确, 取消不算 intervention 行为.

**Task 表:**

| Task | Red Test Name | 关键要点 | Commit Message |
|---|---|---|---|
| 4.1 | `test_chat_event_metadata_optional_default_none` (tests/test_chat_session.py) | `ChatEvent(type="x", content="y").metadata is None` | `chat/session · Phase 16.2 Task 4.1: ChatEvent.metadata 字段 default None` |
| 4.2 | `test_chat_event_metadata_explicit_dict` (tests/test_chat_session.py) | `ChatEvent(type="x", content="y", metadata={"k":"v"}).metadata == {"k":"v"}` | `chat/session · Phase 16.2 Task 4.2: metadata explicit dict 保留` |
| 4.3 | `test_handle_predict_event_carries_intervention_metadata` (tests/test_chat_slash_commands.py) | mock `input_provider` 返 "假设 X", mock `prediction_predict`, 调 `_handle_predict` (raw handler, 不经 wrapper), 最后 event `.metadata == {"intervention":"假设 X"}` | `chat/slash · Phase 16.2 Task 4.3: _handle_predict 加 intervention metadata` |
| 4.4 | `test_handle_counterfactual_event_carries_intervention_metadata` | 同上, `/counterfactual` | `chat/slash · Phase 16.2 Task 4.4: _handle_counterfactual 加 intervention metadata` |
| 4.5 | `test_handle_predict_cancel_no_intervention_metadata` | `input_provider` 返 "q", 取消分支 event `.metadata is None` (无 intervention) | `chat/slash · Phase 16.2 Task 4.5: 取消分支不带 intervention` |

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_session.py tests/test_chat_slash_commands.py -v
.venv/bin/ruff check src/explain_engine/chat/session.py src/explain_engine/chat/slash_commands.py
```

---

## Wave 5: `_handle_history` Slash 命令

**目标:** 新 `/history` slash 命令注册 + 实现 (含 `--limit` / `--type` 解析 + 输出渲染 + 边界 case 13).

**依赖:** Wave 1 (storage load) + Wave 8 (chat_copy 常量, 可 Wave 5 后做 Wave 8 也行 — 但 Wave 8 zero-dep 推荐先做)

**文件:**
- Modify: `src/explain_engine/chat/slash_commands.py` (`_handle_history` + 注册到 DEFAULT_COMMANDS)
- Test: `tests/test_chat_slash_commands.py` (新 class `TestHandleHistory`)

**技术参考 — Green 实现骨架 (注意, 不用 argparse, 手 parse 即可避免 typer/click 噪音):**

```python
# src/explain_engine/chat/slash_commands.py

_HISTORY_VALID_TYPES = {"slash", "llm_turn"}
_HISTORY_LIMIT_MAX = 200
_HISTORY_LIMIT_DEFAULT = 30

def _parse_history_args(args: list[str]) -> tuple[int, set[str] | None, str | None]:
    """返 (limit, types_filter_or_None_for_all, err_msg_or_None)."""
    from explain_engine.chat.chat_copy import (
        err_history_limit_range, err_history_limit_type,
        err_history_type_invalid, err_history_positional,
    )
    limit = _HISTORY_LIMIT_DEFAULT
    types: set[str] | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--all":
            limit = _HISTORY_LIMIT_MAX
            i += 1
        elif a == "--limit":
            if i + 1 >= len(args):
                return (0, None, err_history_limit_type())
            try:
                limit = int(args[i + 1])
            except ValueError:
                return (0, None, err_history_limit_type())
            if limit < 1:
                return (0, None, err_history_limit_type())
            if limit > _HISTORY_LIMIT_MAX:
                return (0, None, err_history_limit_range(limit))
            i += 2
        elif a == "--type":
            # 收一个或多个后续 token 直到下一个 -- 或末尾
            types_collected = []
            j = i + 1
            while j < len(args) and not args[j].startswith("--"):
                types_collected.append(args[j])
                j += 1
            if not types_collected:
                return (0, None, err_history_type_invalid("(空)"))
            for t in types_collected:
                if t not in _HISTORY_VALID_TYPES:
                    return (0, None, err_history_type_invalid(t))
            types = set(types_collected)
            # 多选等价 None (全显), dedup 后若 == 全集则视为无过滤
            if types == _HISTORY_VALID_TYPES:
                types = None
            i = j
        else:
            return (0, None, err_history_positional())
    return (limit, types, None)


def _render_history_entry_full(entry: dict) -> str:
    """渲染单 entry, 不截断 (用于 /history 输出)."""
    from explain_engine.chat.chat_copy import (
        HISTORY_INTERVENTION_PREFIX, HISTORY_SUMMARY_PREFIX,
        HISTORY_TYPE_PREFIX_USER, HISTORY_TYPE_PREFIX_ASSISTANT,
    )
    ts = entry.get("ts", "?")[:19].replace("T", " ")  # 截到秒
    typ = entry.get("type")
    if typ == "slash":
        lines = [f"[{ts}] /{entry.get('cmd', '?')}"]
        if "intervention" in entry:
            lines.append(f"  {HISTORY_INTERVENTION_PREFIX}{entry['intervention']}")
        lines.append(f"  {HISTORY_SUMMARY_PREFIX}{entry.get('summary', '?')}")
        return "\n".join(lines)
    elif typ == "llm_turn":
        return (
            f"[{ts}] {HISTORY_TYPE_PREFIX_USER}{entry.get('user_input', '')}\n"
            f"  {HISTORY_TYPE_PREFIX_ASSISTANT}{entry.get('assistant_text', '')}"
        )
    return f"[{ts}] (unknown entry type: {typ})"


async def _handle_history(chat, args: list[str]):
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.chat_copy import HISTORY_HEADER, HISTORY_FOOTER

    limit, types, err = _parse_history_args(args)
    if err is not None:
        return [ChatEvent(type="slash_error", content=err)]

    if getattr(chat, "is_ephemeral", False) or getattr(chat, "sid", None) is None:
        return [ChatEvent(type="slash_history", content="(ephemeral 会话, 无历史可查)")]

    try:
        all_entries = chat.storage.load_repl_history(chat.sid)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=f"读取历史失败: {type(exc).__name__}: {exc}")]

    if types is not None:
        filtered = [e for e in all_entries if e.get("type") in types]
    else:
        filtered = all_entries

    total = len(filtered)
    shown_entries = filtered[-limit:] if total > limit else filtered

    if not shown_entries:
        from explain_engine.chat.chat_copy import BANNER_HISTORY_EMPTY
        return [ChatEvent(type="slash_history", content=BANNER_HISTORY_EMPTY)]

    body = "\n\n".join(_render_history_entry_full(e) for e in shown_entries)
    out = (
        HISTORY_HEADER.format(total=total, shown=len(shown_entries))
        + "\n\n" + body + "\n\n" + HISTORY_FOOTER
    )
    return [ChatEvent(type="slash_history", content=out)]
```

**注册:** `DEFAULT_COMMANDS` 列表加 `SlashCommand(name="history", description=COMMAND_DESCRIPTIONS["history"], handler=_wrap_handler("history", _handle_history))`.

**注意:** `/history` 自己也走 wrapper, 但 history handler 自身不改 graph → snapshot delta = `"无变化"`, history entry 还会有一条 `/history` slash entry. 这是预期 (用户查 history 这个动作本身也应该有 record). Wave 5 测试需 mock 此细节或接受.

**Task 表:**

| Task | Red Test Name (`TestHandleHistory` class) | 关键要点 | Commit Message |
|---|---|---|---|
| 5.1 | `test_handle_history_default_shows_last_30` | mock `storage.load_repl_history` 返 50 entry, output 含 "显示最近 30 条" + 30 entry 渲染 | `chat/slash · Phase 16.2 Task 5.1: /history 默认 limit 30` |
| 5.2 | `test_handle_history_limit_5` | args=`["--limit","5"]`, output 5 entry | `chat/slash · Phase 16.2 Task 5.2: /history --limit 5` |
| 5.3 | `test_handle_history_limit_exceeds_total` | --limit 100, mock 7 entry, output 7 + total=7 shown=7 | `chat/slash · Phase 16.2 Task 5.3: limit 超 total 显实际数` |
| 5.4 | `test_handle_history_type_slash_filters` | mock 5 slash + 3 llm_turn, args=`["--type","slash"]`, output 仅 5 slash | `chat/slash · Phase 16.2 Task 5.4: --type slash 过滤` |
| 5.5 | `test_handle_history_type_multi_equals_no_filter` | args=`["--type","slash","llm_turn"]`, 等价无 type (全 8 entry) | `chat/slash · Phase 16.2 Task 5.5: --type 多选 = 等价无过滤` |
| 5.6 | `test_handle_history_type_dedup` | args=`["--type","slash","slash"]`, 等价 `--type slash` | `chat/slash · Phase 16.2 Task 5.6: --type 重复 dedup` |
| 5.7 | `test_handle_history_limit_invalid_int` | args=`["--limit","abc"]` → slash_error event, content 含 "需为 1-200 整数" | `chat/slash · Phase 16.2 Task 5.7: --limit 非整数 reject` |
| 5.8 | `test_handle_history_limit_zero_or_negative` | args=`["--limit","0"]` 和 `["--limit","-1"]` 都 reject | `chat/slash · Phase 16.2 Task 5.8: --limit 0 / 负数 reject` |
| 5.9 | `test_handle_history_limit_above_200` | args=`["--limit","201"]` → slash_error 含 "上限 200" | `chat/slash · Phase 16.2 Task 5.9: --limit > 200 reject` |
| 5.10 | `test_handle_history_type_invalid_value` | args=`["--type","foo"]` → slash_error 含 "slash / llm_turn" | `chat/slash · Phase 16.2 Task 5.10: --type 非法值 reject` |
| 5.11 | `test_handle_history_positional_arg_rejected` | args=`["foo"]` → slash_error 含 "不接位置参数" | `chat/slash · Phase 16.2 Task 5.11: 位置参数 reject` |
| 5.12 | `test_handle_history_empty_session` | mock load 返 `[]`, output 含 "无历史" | `chat/slash · Phase 16.2 Task 5.12: 空 history 友好提示` |
| 5.13 | `test_handle_history_intervention_full_not_truncated` | entry intervention 500 字, output 完整 500 字 (不截 80) | `chat/slash · Phase 16.2 Task 5.13: /history intervention 不截断` |
| 5.14 | `test_handle_history_llm_turn_full_not_truncated` | entry user_input + assistant_text 各 200 字, output 完整 (不截 60) | `chat/slash · Phase 16.2 Task 5.14: /history llm_turn 不截断` |

**额外:** Wave 5 末尾 1 commit 把 `_handle_history` 注册进 `DEFAULT_COMMANDS` + `HELP_GROUPS_ZH` 加进 "session 管理" 组. 用既有 `test_handle_help_lists_all_commands` 验证 `/help` 含 `history`.

| Task | 实现 | Commit |
|---|---|---|
| 5.15 | `DEFAULT_COMMANDS` 加 `SlashCommand("history", ...)`, `HELP_GROUPS_ZH` "session 管理" 组加 `"history"`, 跑既有 help test 全绿 | `chat/slash · Phase 16.2 Task 5.15: /history 注册到 DEFAULT_COMMANDS + HELP` |

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestHandleHistory tests/test_chat_slash_commands.py::test_handle_help_lists_all_commands -v
.venv/bin/ruff check src/explain_engine/chat/slash_commands.py
```

---

## Wave 6: `process_user_turn` llm_turn 写入

**目标:** `chat/session.py` `process_user_turn` (或对应入口函数, Wave 6 Task 6.1 先 grep 确认实际函数名) 完整结束后 append type=llm_turn entry. 异常 / SIGINT 分支不写.

**依赖:** Wave 1 (storage)

**文件:**
- Modify: `src/explain_engine/chat/session.py`
- Test: `tests/test_chat_session.py`

**先决步骤 (Task 6.0, 不单独 commit):** grep `session.py` 找处理用户自然语言输入的入口函数名 (可能叫 `process_user_turn` / `handle_user_message` / loop 内 inline). 确认写入点位置.

```bash
grep -n "user_input\|process_user\|append_transcript" src/explain_engine/chat/session.py | head -30
```

**技术参考 — Green 实现 (假设入口是 `process_user_turn` 或类似 async method):**

```python
# src/explain_engine/chat/session.py — 在 process_user_turn (或类似函数) return assistant_text 前加

# (已有 LLM 调用 + tool_use 循环, 最终拿到 assistant_text)
# ... 不动 ...

# 新增 history 写入 (异常/中断分支不到这里)
if not getattr(self, "is_ephemeral", False) and self.sid is not None:
    from datetime import datetime, timezone
    try:
        self.storage.append_repl_history(self.sid, {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
            "type": "llm_turn",
            "user_input": user_input,
            "assistant_text": assistant_text,
        })
    except Exception as exc:
        # 跟 slash wrapper 同样: silent + log warn
        logger.warning(f"append llm_turn history failed: {type(exc).__name__}: {exc}")

return assistant_text  # 原 return 不动
```

**Task 表:**

| Task | Red Test Name (tests/test_chat_session.py, 新 class `TestLLMTurnHistory`) | 关键要点 | Commit Message |
|---|---|---|---|
| 6.1 | `test_process_user_turn_appends_llm_turn_history` | mock LLM 返 "回答 X", 调入口函数("问题 Y"), storage.load_repl_history 返 1 entry: type=llm_turn user_input="问题 Y" assistant_text="回答 X" | `chat/session · Phase 16.2 Task 6.1: process_user_turn append llm_turn entry` |
| 6.2 | `test_process_user_turn_llm_failure_no_append` | mock LLM 抛 `LLMError`, 跑后 `load_repl_history == []` (无 partial entry) | `chat/session · Phase 16.2 Task 6.2: LLM 异常分支不写 history` |
| 6.3 | `test_process_user_turn_keyboard_interrupt_no_append` | mock LLM 抛 `KeyboardInterrupt`, jsonl 不存在 | `chat/session · Phase 16.2 Task 6.3: SIGINT 不写 history` |
| 6.4 | `test_process_user_turn_tool_use_intermediate_not_appended` | mock LLM 多轮 tool_use (n=3 中间) + final assistant text, jsonl 仅 1 entry (final), 中间 tool 不写 | `chat/session · Phase 16.2 Task 6.4: tool_use 中间不写, 仅 final` |

**风险:** Task 6.0 grep 可能发现 `session.py` 没有现成 "纯 user turn" 入口 (loop 内 inline 处理). 此时 6.1 实现需找到 loop 内合适 hook 点, 或包 helper 函数. 若改动大于 30 行, 拆为 6.0a (refactor 抽函数, 不引功能) + 6.1 (加 history write).

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_session.py::TestLLMTurnHistory tests/test_chat_session.py -v
.venv/bin/ruff check src/explain_engine/chat/session.py
```

---

## Wave 7: Resume Banner `_render_recent_history`

**目标:** 新模块或 `chat/repl_entry.py` (或 session.py) 加 `_render_recent_history(entries, max_n=10) -> str` 渲染最近 N 条 (旧→新, 字数截断). Resume 入口调它把结果拼到 banner.

**依赖:** Wave 1 (storage load) + Wave 8 (chat_copy 文案常量)

**文件:**
- Create: `tests/test_chat_repl_history.py` (新文件)
- Modify: `src/explain_engine/chat/repl_entry.py` 或 `session.py` (Wave 7 Task 7.0 先 grep 确认 resume banner 拼接位置)

**先决步骤 (Task 7.0):** grep `banner` / `resume` 找拼装 resume banner 字符串的位置:
```bash
grep -rn "已恢复 session\|resume.*banner\|概念库:" src/explain_engine/chat/
```

**技术参考 — Green 实现:**

```python
# src/explain_engine/chat/repl_entry.py 或新 chat/history_render.py

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."

def _format_short_ts(iso_ts: str) -> str:
    """ISO → '05-25 14:23' (12 字符)."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_ts[:12] if iso_ts else "?"

def _render_history_entry_short(entry: dict) -> str:
    """单 entry 短版 (banner 用, 字数截断)."""
    from explain_engine.chat.chat_copy import (
        HISTORY_INTERVENTION_PREFIX, HISTORY_TYPE_PREFIX_USER,
        HISTORY_TYPE_PREFIX_ASSISTANT,
    )
    ts = _format_short_ts(entry.get("ts", ""))
    typ = entry.get("type")
    if typ == "slash":
        head = f"  [{ts}] /{entry.get('cmd', '?'):<18} {entry.get('summary', '?')}"
        if "intervention" in entry:
            iv = _truncate(entry["intervention"], 80)
            head += f"\n                {HISTORY_INTERVENTION_PREFIX}{iv}"
        return head
    elif typ == "llm_turn":
        ui = _truncate(entry.get("user_input", ""), 60)
        at = _truncate(entry.get("assistant_text", ""), 60)
        return (
            f"  [{ts}] {HISTORY_TYPE_PREFIX_USER}{ui}\n"
            f"                {HISTORY_TYPE_PREFIX_ASSISTANT}{at}"
        )
    return f"  [{ts}] (unknown type)"

def render_recent_history(entries: list[dict], max_n: int = 10) -> str:
    """Banner 用 — 最近 N 条 (旧→新). 空返友好提示."""
    from explain_engine.chat.chat_copy import (
        BANNER_HISTORY_HEADER, BANNER_HISTORY_EMPTY, BANNER_HISTORY_FOOTER,
    )
    if not entries:
        return f"\n  {BANNER_HISTORY_EMPTY}\n"
    # 按 ts 升序 sort (旧→新), 取末尾 N
    sorted_entries = sorted(entries, key=lambda e: e.get("ts", ""))
    shown = sorted_entries[-max_n:]
    header = BANNER_HISTORY_HEADER.format(n=len(shown))
    body = "\n".join(_render_history_entry_short(e) for e in shown)
    return f"\n  {header}\n{body}\n\n  {BANNER_HISTORY_FOOTER}\n"
```

**Task 表:**

| Task | Red Test Name (`TestRenderRecentHistory` class, tests/test_chat_repl_history.py) | 关键要点 | Commit Message |
|---|---|---|---|
| 7.1 | `test_render_recent_history_basic_5_entries` | 输入 5 slash entry, 输出含 "最近 5 条" header + 5 行 + footer | `chat/repl_entry · Phase 16.2 Task 7.1: render_recent_history 基础 5 entry` |
| 7.2 | `test_render_recent_history_max_10_caps_long_list` | 输入 30 entry, 输出仅最后 10 (按 ts 排序后取末 10) | `chat/repl_entry · Phase 16.2 Task 7.2: 超 max_n 截到末 N` |
| 7.3 | `test_render_recent_history_old_to_new_order` | 输入打乱时序 list, 输出按 ts 升序 (旧→新) | `chat/repl_entry · Phase 16.2 Task 7.3: ts 升序排序` |
| 7.4 | `test_render_recent_history_intervention_truncated_80` | intervention 500 字, banner 含 `首 80 字...` | `chat/repl_entry · Phase 16.2 Task 7.4: intervention 80 字截断` |
| 7.5 | `test_render_recent_history_llm_turn_truncated_60` | user_input / assistant_text 各 200 字, banner 含 `首 60 字...` 各 | `chat/repl_entry · Phase 16.2 Task 7.5: llm_turn 60 字截断` |
| 7.6 | `test_render_recent_history_error_entry_marker` | entry 含 `error="LLMError: x"` + `summary="(执行失败: LLMError)"`, banner 行显失败 summary | `chat/repl_entry · Phase 16.2 Task 7.6: error entry summary 标失败` |
| 7.7 | `test_render_recent_history_empty_friendly_msg` | 空 list, 输出含 BANNER_HISTORY_EMPTY 文本 | `chat/repl_entry · Phase 16.2 Task 7.7: 空 history 友好提示` |
| 7.8 | `test_render_recent_history_unknown_delta` | entry summary=`"(变化未知)"`, banner 原样显 | `chat/repl_entry · Phase 16.2 Task 7.8: unknown summary 透传` |
| 7.9 | `test_resume_banner_includes_recent_history_section` | 把 render_recent_history 接到 resume 入口, 调 resume → banner string 含 `"最近 N 条操作"` 段 | `chat/repl_entry · Phase 16.2 Task 7.9: resume banner 拼 history 段` |

**额外 (Task 7.10):** resume 路径加 load_repl_history 调用 + 调用 render_recent_history. 失败 silent (整段不显, log warn).

| Task | 实现 | Commit |
|---|---|---|
| 7.10 | resume 入口 (chat/repl_entry.py 或 session.py 找到的 banner 拼装函数) 加: `history_entries = chat.storage.load_repl_history(sid)`, 包 try/except, 失败时 `history_section = ""` + log warn, success 时 `history_section = render_recent_history(history_entries, 10)`, 拼到既有 banner string. | `chat/repl_entry · Phase 16.2 Task 7.10: resume 入口 load + render history, 降级 silent` |

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_repl_history.py -v
.venv/bin/ruff check src/explain_engine/chat/repl_entry.py src/explain_engine/chat/session.py tests/test_chat_repl_history.py
```

---

## Wave 8: chat_copy.py 文案常量

**目标:** 把 design §6.3 列的所有常量 + lambda err 函数加到 `chat_copy.py`. 这是 single source, 应在 Wave 5/7 之前完成 (但 zero-dep, 实际任何 wave 前/后都可).

**依赖:** 无

**文件:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**技术参考 — 完整新增内容:**

```python
# src/explain_engine/chat/chat_copy.py 末追加

# ── Phase 16.2: REPL History Persistence ──

COMMAND_DESCRIPTIONS["history"] = "查看本 session 操作历史 (默认最近 30 条)"

# HELP_GROUPS_ZH "管理 session" 组加 "history" (具体位置 grep 后插入)
# 例: ("管理 session", ["new", "resume", "list", "lexicon", "theories", "theory", "history"])

BANNER_HISTORY_HEADER = "─── 最近 {n} 条操作 (旧 → 新) ───"
BANNER_HISTORY_EMPTY = "(本 session 无历史操作记录)"
BANNER_HISTORY_FOOTER = "输 /history 看完整历史, /help 看所有命令"

HISTORY_HEADER = "本 session 共 {total} 条历史记录, 显示最近 {shown} 条 (旧 → 新):"
HISTORY_FOOTER = "(输入 /history --type slash 仅看命令, --type llm_turn 仅看对话, --limit N 调数量)"
HISTORY_TYPE_PREFIX_USER = "你: "
HISTORY_TYPE_PREFIX_ASSISTANT = "Claude: "
HISTORY_INTERVENTION_PREFIX = "假设: "
HISTORY_SUMMARY_PREFIX = "概要: "
HISTORY_FAILED_SUMMARY = "(执行失败: {error_type})"

def err_history_limit_range(v) -> str:
    return f"错误: --limit 上限 200, 当前传入 {v}."

def err_history_limit_type() -> str:
    return "错误: --limit 需为 1-200 整数."

def err_history_type_invalid(v) -> str:
    return f"错误: --type 取值为 slash / llm_turn (可多选, 空格分隔). 收到: {v!r}"

def err_history_positional() -> str:
    return "错误: /history 不接位置参数, 用 --limit / --type."
```

**Task 表:**

| Task | Red Test Name (tests/test_chat_copy.py 新 class `TestHistoryCopy`) | 关键要点 | Commit Message |
|---|---|---|---|
| 8.1 | `test_chat_copy_banner_history_constants_exist` | 3 个 BANNER_HISTORY_* import 成功, 全非空中文; HEADER 含 `{n}` placeholder | `chat/chat_copy · Phase 16.2 Task 8.1: BANNER_HISTORY_* 3 常量` |
| 8.2 | `test_chat_copy_history_command_constants_exist` | HISTORY_HEADER / FOOTER / 4 prefix + HISTORY_FAILED_SUMMARY import 成功 | `chat/chat_copy · Phase 16.2 Task 8.2: HISTORY_* 6 常量 + FAILED_SUMMARY` |
| 8.3 | `test_chat_copy_command_descriptions_has_history` + `test_chat_copy_history_err_funcs_callable` | `COMMAND_DESCRIPTIONS["history"]` 存在中文; 4 err 函数 callable, 输出含相关关键词 (`200`, `slash`, etc) | `chat/chat_copy · Phase 16.2 Task 8.3: COMMAND_DESCRIPTIONS["history"] + 4 err 函数` |

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_copy.py -v
.venv/bin/ruff check src/explain_engine/chat/chat_copy.py
```

---

## Wave 9: 端到端 Smoke 测试

**目标:** 真跑 (用真 StorageV2 + tmp EXPLAIN_HOME + FakeLLM) 验证: 走完几个 slash → 重 load → banner 显. 不模拟 dispatcher 中央 wrapper, 走真实 wrapped handler.

**依赖:** Wave 1-8 全部

**文件:**
- Modify: `tests/test_chat_repl_history.py` (Wave 7 创建, 在此加 e2e class)

**Task 表:**

| Task | Red Test Name (`TestE2EReplHistory` class) | 关键要点 | Commit Message |
|---|---|---|---|
| 9.1 | `test_e2e_slash_predict_writes_history_visible_on_resume` | tmp EXPLAIN_HOME, FakeLLM, mock prediction_predict 改 state.graph 加 1 L1 + 5 L0 + 12 edges, 走 `_handle_predict` (经 wrapper), `load_repl_history` 返 1 entry: cmd=predict, intervention 完整, summary 含 `+1 L1` `+5 现象` `+12 边` | `chat/repl_history · Phase 16.2 Task 9.1: e2e /predict 写 history + 重 load 完整` |
| 9.2 | `test_e2e_resume_banner_contains_recent_ops` | 同 9.1 setup + 调 render_recent_history(10), 输出含 "最近 1 条" header + cmd /predict + 截 80 字 intervention | `chat/repl_history · Phase 16.2 Task 9.2: e2e banner 渲染含 history 段` |
| 9.3 | `test_e2e_history_command_after_session_with_ops` | 串调 /compress + /run + /predict (各 wrapped), `_handle_history(chat, [])` 输出含 3 entry + 各自 summary | `chat/repl_history · Phase 16.2 Task 9.3: e2e /history 命令完整` |
| 9.4 | `test_e2e_old_session_no_repl_history_resume_friendly` | tmp 手造老 session (sid 目录有 metadata.json + graph.json, 无 repl_history.jsonl), 调 resume banner 拼装, 含 BANNER_HISTORY_EMPTY 文本, 不 crash | `chat/repl_history · Phase 16.2 Task 9.4: e2e 老 session 无 history 兼容` |

**Verify 命令:**
```bash
.venv/bin/python -m pytest tests/test_chat_repl_history.py -v
.venv/bin/ruff check tests/test_chat_repl_history.py
```

---

## Wave 10: Acceptance Doc + 全量 Verify

**目标:** 写 acceptance 文档 + 跑全量 test + ruff + 手动 smoke 4 场景 + 记 commit hash list.

**依赖:** Wave 1-9 全部

**Task 10.1: Acceptance doc 草稿**

文件: `docs/plans/2026-05-25-repl-history-persistence-acceptance.md` (新建)

内容大纲:
1. 头部 (Date / Branch / Related)
2. Wave 1-10 commit hash list (`git log --oneline | grep "Phase 16.2"`)
3. 全量 verify 命令输出
4. 4 个手动 smoke 场景 (依 design §6 / 用户验收口径):
   - (a) 跑 `/predict` 输 intervention → 退 chat → 重进 → `/resume` 选同 sid → banner 显 `/predict` + intervention 前 80 字
   - (b) `/history` 输出含完整 intervention 文本
   - (c) `/history --type llm_turn` 仅显自然语言对话回合
   - (d) 老 session (Phase 16 前创的) `/resume` 后 banner 显 "(本 session 无历史操作记录)" 不 crash
5. 既有 1069+ test 仍全绿
6. ruff 全绿
7. 风险点 retro (Wave 10 实际跑下来遇到的实际问题)

Commit: `docs · Phase 16.2 Task 10.1: acceptance smoke doc + 全量 verify`

---

## 完整 Acceptance Verify 命令

```bash
# 1. 全量 pytest (1069+ 既有 + ~55 新 = ~1125)
.venv/bin/python -m pytest tests/ --tb=short -q

# 2. ruff 全绿
.venv/bin/ruff check src/ tests/

# 3. Wave 10 手动 smoke (走真实 chat REPL)
.venv/bin/python -m explain_engine.cli chat   # 进 ephemeral REPL
# 输自然语言问题 → bootstrap → /predict → 假设 X → 退 → 重进 → /resume → 选刚刚 sid
# 检查 banner 含 "最近 N 条操作" 段, 含 /predict + 截 80 字 intervention

# 4. commit hash list (~50 commit)
git log --oneline --grep "Phase 16.2"
```

---

## 风险点 + 应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| `session.py` 没有清晰的 `process_user_turn` 入口, loop 内 inline 处理 LLM 调用 | 中 | Wave 6 实现复杂度 +50% | Task 6.0 先 grep, 若需 refactor 抽函数, 拆 6.0a (refactor 不改行为) + 6.1 (加 history) |
| `_wrap_handler` 应用到 21 个 handler 后某 handler 测试挂 (e.g. handler 返非 list) | 中 | Wave 3.9 回滚 | Task 3.9 先 `git stash`, 跑全量 test 确认零回归再 commit; 若挂, debug single handler |
| Banner 渲染汉字宽度对齐 (左对齐 20 字符) 在终端实际显示偏差 (因汉字双宽) | 低 | UI 视觉小瑕疵 | MVP 不处理, 接受. Phase 17+ 加 wcwidth 计算如果用户反馈 |
| 60 字 / 80 字截断对中文 char vs byte 含义混淆 | 中 | 实际显示长度不一致 | Python 字符串 len() 是 char 数, 不是 byte 数. 中文每 char 1 单位, ASCII 也 1 单位. 视觉宽度差异接受 |
| ChatEvent.metadata 加 `field(default_factory=dict)` vs `None` | 低 | 测试期望偏差 | 严格用 `None` (design 已定), 测试用 `is None` 验, 不用 `== {}` |
| /history 走 wrapper 自身也产 history entry, 测试 mock 不到 | 中 | Wave 5 测试干扰 | Wave 5 测试调 `_handle_history` raw (不经 wrapper); 或在 fixture 把 _wrap_handler 改 no-op |
| `_history_logger` name 冲突 (chat/slash 已有别的 logger) | 低 | log 重复 | grep slash_commands.py 现有 `logger = logging.getLogger(...)`, 复用同 logger 名 |
| 老 session resume 无 sidecar, `chat.storage` 可能 None (ephemeral 路径) | 低 | AttributeError | Wave 7.10 + Wave 5 / 6 都加 `getattr(chat, "storage", None) is None` 守护 |
| 64 task 串行 1 天紧 | 中 | 时间超 | Wave 1+2+8 并行 (zero-dep), 节省 ~3 hr; 极端情况 Wave 10 acceptance 拆到第二天 |
| 既有 test 期待 ChatEvent 只有 2 字段 (用 == 比较 dataclass) | 中 | 加 metadata 后 ChatEvent equality 变 | 检索 `ChatEvent(` 在 tests/ 出现, 若有 `==` 验证 ChatEvent 全等, 改成字段级比较或加 metadata=None 显式 |

---

## 进入执行 Skill 选项

Plan 写完. 下一步两个执行方式可选:

**1. Subagent-Driven (本 session 内)**: 每 task 派 fresh subagent + code review checkpoint, 快速迭代. 调 `superpowers:subagent-driven-development`.

**2. Parallel Session (单开)**: 在 worktree 开新 session 跑 `superpowers:executing-plans`, batch 执行带 checkpoint.

按 user prompt 当前任务仅出 plan, **不进** subagent-driven-development. 由用户下次决定走哪个执行方式.
