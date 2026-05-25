# Phase 16.2: Per-Session REPL History Persistence 设计

**Date**: 2026-05-25
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 9 Wave B+ (storage_v2 + transcript.jsonl)
- Phase 11 Wave 3/4 (slash dispatcher + 6 single-session slash + 3 cross-session slash)
- Phase 14 (Chat Stage Flow + Hints — 5 slash 推 stage)
- Phase 15 (Chat REPL 去技术化 — `chat_copy.py` 文案 single source)
- Phase 16 (Theory Formation — bootstrap inject + /theories /theory slash)

---

## 1. Motivation

Phase 9-16 落地了完整 chat REPL + cross-session graph + theory formation, 但 session 内部
**用户操作历史** 不可恢复:

- `transcript.jsonl` 只为 LLM context 设计 (严格 Anthropic API messages schema), slash
  命令明确 **不入** transcript (`chat/session.py:256` 注释: "slash 不 append transcript").
- `graph.json` 记录了 graph mutation + reasoning_trace, 但拿不到用户**做了什么操作 +
  intervention 输入什么 + 结果摘要**.
- `/predict` / `/counterfactual` 收的 intervention text (假设描述) **完全不存**, 重启即
  丢失. 复现实验只能靠人脑记忆 — 跟 Phase 16 "stable theory 跨 session 累积"
  的可重复性追求矛盾.

**Phase 16.2 目标**: 加 sidecar `repl_history.jsonl` 持久化每 session 的用户操作历史
(slash 命令 + intervention text + LLM 对话回合), 仅供 resume 时回忆 + `/history` 命令
查询, **不进 LLM context** (跟 transcript 完全独立).

## 2. Goals

1. **Sidecar 持久化**: 新文件 `~/.explain/projects/<pid>/sessions/<sid>/repl_history.jsonl`,
   append-only, 跟 transcript.jsonl 同目录但 schema 完全独立.
2. **3 type entry**: slash (无 intervention) / slash (含 intervention) / llm_turn (自然
   语言对话回合). 完整存储原始文本, 不在写盘时截断.
3. **中央 wrapper 零侵入**: 21 个现有 slash handler 完全不动 (除 `/predict` `/counterfactual`
   各加 1 行 metadata), dispatcher 加一层 wrapper 自动 snapshot graph + write history.
4. **Resume banner 自动展示最近 10 条**: 旧→新顺序, intervention 截 80 字, llm_turn
   user_input / assistant_text 截 60 字.
5. **新 slash 命令 `/history`**: 默认显最近 30 条, 支持 `--limit` (1-200) / `--type`
   (slash / llm_turn 可多选). 输出 **不截断** (跟 banner 不同).
6. **降级第一**: history 写/读任何环节失败都不能影响 chat 主流程.

## 3. Non-Goals

- **跨 session history 聚合** — 仅 per-session, 不做 project-level "全局操作流".
- **History 进 LLM context** — 严格不喂 LLM (跟 transcript 互补关系, 不重叠).
- **History 编辑 / 删除** — append-only, 用户改坏文件 = silent skip, 但不提供 slash
  操作.
- **多轮 tool_use 中间过程** — 仅存最终 assistant 自然语言文本, 中间 tool 调用不入
  history (那是 transcript 该管的).
- **流式部分输出落盘** — LLM 流式被中断 (SIGINT) 仅记完整回合, 半截不写.
- **History 跨机器同步 / 远程备份** — 跟 graph.json transcript.jsonl 同等地位, 用户
  自己负责.
- **Banner 内分页 / 超长滚动** — 10 条上限内可控, /history 默认 30 自适应屏高.
- **Snapshot 加 lifecycle:stale/decayed 维度** — 仅 4 count (l0/l1/l2/edges) 够用,
  decayed delta 算 negative l*.

## 4. Design

### 4.1 Architecture overview

```
新增 1 个 storage 方法对 (persistence/storage_v2.py +20 LOC)
   ├── append_repl_history(sid, entry: dict)   # jsonl append, 复用 append_transcript 模
   └── load_repl_history(sid) -> list[dict]    # 损坏行 silent skip + log warn (rate-limited)

新增 1 个 dispatcher wrapper (chat/slash_commands.py +60 LOC)
   ├── _snapshot_graph(state) -> dict          # 4 count: l0/l1/l2/edges
   ├── _snapshot_graph_safe(state) -> dict|None  # 包 try, 失败返 None
   ├── _compute_delta(before, after) -> str    # "+1 L1 / +5 现象 / +12 边"
   └── _wrap_handler(name, handler)            # decorator: snapshot → call → write history
                                                # handler 异常时写带 error 字段的 entry 后再 raise
                                                # storage write 失败 silent + log warn

新增 1 个 history 命令 (chat/slash_commands.py +50 LOC)
   ├── _handle_history(chat, args)             # 解析 --limit / --type, 渲染输出
   └── COMMAND_DESCRIPTIONS["history"] + chat_copy 文案常量

修改 1 个 resume 路径 (chat/session.py 或 repl_entry.py +15 LOC)
   ├── _render_recent_history(history, n=10) -> str
   └── 旧 banner (graph 摘要) 保留, 末尾追"最近 N 条操作"段

修改 1 个 llm_turn 写入路径 (chat/session.py +10 LOC)
   └── process_user_turn 完整结束后 append type=llm_turn entry
       (异常 / KeyboardInterrupt 分支不写)

ChatEvent 扩展 1 个 optional 字段 (chat/session.py +1 行)
   └── metadata: dict | None = None
       仅 _handle_predict / _handle_counterfactual 用, 塞 {"intervention": text}
```

### 4.2 数据流

```
用户输 /predict
  ↓
repl_entry.async loop 解析 → 识别为 slash
  ↓
slash_commands._wrap_handler("predict", _handle_predict).call()
  │
  ├── before = _snapshot_graph_safe(chat.state)        # {l0:21, l1:5, l2:0, edges:89}
  ├── (handler 内部弹 prompt 收 intervention → 调 prediction_predict → mutate state.graph)
  ├── result = await _handle_predict(chat, args)       # 返 list[ChatEvent], 最后一个含 metadata
  ├── after = _snapshot_graph_safe(chat.state)         # {l0:26, l1:6, l2:0, edges:101}
  ├── delta = _compute_delta(before, after)            # "+1 L1 / +5 现象 / +12 边"
  ├── intervention = _extract_intervention(result)     # 读最后 event.metadata["intervention"]
  └── storage.append_repl_history(sid, {
        "ts": ..., "type":"slash", "cmd":"predict", "args":[],
        "intervention": intervention, "summary": delta
      })  # 失败 silent + log warn
  ↓
return result (透传, dispatch 后续逻辑零变化)


用户输自然语言 "JEPA 跟 GAN 区别?"
  ↓
session.process_user_turn(text)
  ├── ... (现有 LLM 调用 + tool_use 循环不动)
  └── 拿到最终 assistant_text 后:
        storage.append_repl_history(sid, {
          "ts": ..., "type":"llm_turn",
          "user_input": text, "assistant_text": assistant_text
        })
        # LLM 异常 / SIGINT 分支不写
```

### 4.3 跟现有 transcript.jsonl 的关系

| | `transcript.jsonl` (现有) | `repl_history.jsonl` (新) |
|---|---|---|
| 写入路径 | `_send_to_llm` 前后 | dispatcher wrapper + process_user_turn 尾 |
| 读取路径 | 每轮 LLM call 注入 messages= | `_render_recent_history` (banner) + `_handle_history` |
| Schema | Anthropic API messages 严格 | 自由 dict, 3 type 枚举 |
| 写入失败影响 | 致命 (LLM context 丢) | 静默 (banner 缺一段, 主流程继续) |
| Slash 是否入 | 否 | 是 |

### 4.4 关键设计抉择

1. **Wrapper 放 slash_commands.py 而非 session.py**: 因为 `DEFAULT_COMMANDS` registry 在
   slash_commands.py, 在 registry 注册时一次性 wrap 所有 handler 比侵入 session.py loop
   简单. 21 个 handler 完全不知道 wrapper 存在.

2. **Intervention 字段反解走 ChatEvent.metadata**: `/predict` `/counterfactual` 的
   intervention text 在 interactive prompt 收, wrapper 没法在 handler 调前拿到. 方案:
   handler 在 return list 时往最后一个 event 塞 `metadata={"intervention": text}`,
   wrapper 从 event 读. 仅 2 个 handler 改 1 行, 跟"零侵入"原则的唯一妥协.

3. **不存 ChatEvent.content 全文**: content 经常几百字 (示例 /show 输出 60+ 行),
   10 entry × 几百字会让 banner 失控. 只存 `summary` (机器生成短文本) + 必要原始字段
   (intervention / user_input / assistant_text). content 在内存展示后即弃.

4. **Snapshot delta 而非 handler 自报**: 21 个 handler 返回值格式不统一 (有 str 有
   dict), 反 parse 易错. 中央 snapshot 前后 diff 节点数自动算 delta, 跟 handler 解耦.
   代价: `/check` 这种 read-only handler 产生 "无变化" 略空, 但语义正确.

5. **Wrapper 写入时序: handler 后再写**: snapshot before → handler → snapshot after →
   写 entry → return. handler 抛异常时**先写带 error 字段的 entry, 再 raise**.
   KeyboardInterrupt 例外, 直接 propagate 不写 (用户主动放弃).

---

## 5. Data Structure + JSONL Schema

### 5.1 文件路径

```
~/.explain/projects/<project_id>/sessions/<sid>/repl_history.jsonl
                                                    └── 跟 transcript.jsonl 同目录, append-only
```

### 5.2 Entry Schema (3 种 type)

#### Type A: `slash` (无 intervention)

```jsonc
{
  "ts": "2026-05-25T14:23:01.234567+08:00",  // ISO 8601 含时区
  "type": "slash",
  "cmd": "compress",          // 不带 / 前缀 (跟 SlashCommand.name 一致)
  "args": [],                 // list[str], 总是数组
  "summary": "+4 L1 / +0 现象 / +12 边"
}
```

#### Type B: `slash` (含 intervention) — 仅 `/predict` `/counterfactual`

```jsonc
{
  "ts": "2026-05-25T14:25:33.123456+08:00",
  "type": "slash",
  "cmd": "predict",
  "args": [],
  "intervention": "假设 LeCun 提出的 JEPA 真正解决了 c_001 + c_004...",  // 完整存
  "summary": "+1 L1 / +5 现象 / +12 边"
}
```

#### Type C: `llm_turn`

```jsonc
{
  "ts": "2026-05-25T14:30:12.789012+08:00",
  "type": "llm_turn",
  "user_input": "JEPA 跟 GAN 的根本区别是什么?",       // 完整存
  "assistant_text": "JEPA 在 latent space 做预测..."  // 完整存
}
```

#### 失败 entry (handler 抛异常时 wrapper 写入)

```jsonc
{
  "ts": ..., "type": "slash", "cmd": "predict", "args": [],
  "intervention": "...",            // 若已收到
  "summary": "(执行失败: LLMError)",
  "error": "LLMError: rate limit exceeded"  // 仅失败时存
}
```

### 5.3 内部辅助结构 (不入 jsonl)

```python
GraphSnapshot = dict[str, int]  # {"l0": int, "l1": int, "l2": int, "edges": int}

def _snapshot_graph(state) -> GraphSnapshot:
    g = state.graph
    return {
        "l0": sum(1 for n in g.nodes.values() if n.abstraction_level == 0),
        "l1": sum(1 for n in g.nodes.values() if n.abstraction_level == 1),
        "l2": sum(1 for n in g.nodes.values() if n.abstraction_level == 2),
        "edges": len(g.edges),
    }

def _compute_delta(before: GraphSnapshot | None, after: GraphSnapshot | None) -> str:
    if before is None or after is None:
        return "(变化未知)"
    parts = []
    if (d := after["l1"] - before["l1"]):  parts.append(f"{d:+d} L1")
    if (d := after["l0"] - before["l0"]):  parts.append(f"{d:+d} 现象")
    if (d := after["l2"] - before["l2"]):  parts.append(f"{d:+d} L2")
    if (d := after["edges"] - before["edges"]):  parts.append(f"{d:+d} 边")
    return " / ".join(parts) if parts else "无变化"
```

### 5.4 ChatEvent 扩展

```python
@dataclass
class ChatEvent:
    type: str
    content: str | dict | None
    metadata: dict | None = None   # 新增, optional, 默认 None
```

仅 `_handle_predict` / `_handle_counterfactual` 2 个 handler 在 return list 时往最后
event 塞 `metadata={"intervention": text}`. 其他 19 个 handler 完全不动.

### 5.5 时间戳格式

* `datetime.now(timezone.utc).astimezone().isoformat()` 含本地时区
* 精度 microsecond (Python 默认)
* Banner 显示截到分钟: `05-25 14:23`
* `/history` 默认显示秒: `2026-05-25 14:23:01`

### 5.6 Append 原子性

`append_transcript` 现有实现是直接 `f.write()` 单行 — POSIX 保证 < PIPE_BUF (4096B)
的单 write syscall 原子. 单 entry < 1KB, 安全. **不需要** atomic rename.

```python
def append_repl_history(self, sid: str, entry: dict[str, Any]) -> None:
    path = self.session_dir(sid) / "repl_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

### 5.7 Load + 损坏行降级

```python
def load_repl_history(self, sid: str) -> list[dict[str, Any]]:
    path = self.session_dir(sid) / "repl_history.jsonl"
    if not path.exists():
        return []
    out = []
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
                logger.warning(f"repl_history.jsonl line {i+1} corrupt, skip: {e}")
                warn_count += 1
    if total_corrupt > 5:
        logger.warning(f"repl_history.jsonl total {total_corrupt} corrupt lines")
    return out
```

---

## 6. 用户接触面

### 6.1 Resume Banner

#### Phase 16 末态 (无 history)

```
✓ 已恢复 session s_df84e637
  问题:   为什么生成式模型在世界模型研究上被视作瓶颈
  阶段:   已收敛
  概念库: 5 L1 / 0 L2  现象: 21  边: 89
```

#### Phase 16.2 后 (有 history)

```
✓ 已恢复 session s_df84e637
  问题:   为什么生成式模型在世界模型研究上被视作瓶颈
  阶段:   已收敛
  概念库: 5 L1 / 0 L2  现象: 21  边: 89

  ─── 最近 10 条操作 (旧 → 新) ───
  [05-25 14:08] /compress              +4 L1 / +12 边
  [05-25 14:09] /run                   +12 L2 / +37 边
  [05-25 14:11] /rescore               无变化
  [05-25 14:14] 你: JEPA 论文里说的 "abstract causal structure" 具体指什么?
                Claude: JEPA 论文的 "abstract causal structure" 不是经典符号 AI 的...
  [05-25 14:18] /show                  无变化
  [05-25 14:20] /predict               +1 L1 / +5 现象 / +12 边
                假设: 假设 LeCun 提出的 JEPA 真正解决了 c_001 结构先验内化深度 + c_004 组...
  [05-25 14:22] /counterfactual        无变化
                假设: 若把 JEPA 的 latent predictor 换成 pixel-level diffusion, c_005 的 5 个 pre...
  [05-25 14:25] /rescore               无变化
  [05-25 14:27] 你: c_005 跟 c_001 c_004 之间的因果关系强度可信吗?
                Claude: c_005 → c_001/c_004 的两条 manifests_as 关系当前 confidence 0.78/0.82...
  [05-25 14:30] /save                  无变化

  输 /history 看完整历史, /help 看所有命令
```

**对齐规则**:
- `[05-25 14:08]` 13 字符短时间戳
- slash entry: `/cmd` 左对齐 20 字符 + summary 右接
- llm_turn entry: `你:` + 一行 (60 字截) + 换行 + 16 空格缩进 `Claude:` + 一行 (60 字截)
- intervention entry: 第二行 16 空格缩进 `假设:` + 80 字截 + `...`

#### 边界 case

| Case | Banner 行为 |
|---|---|
| 无 history (老 session 或新空 session) | `(本 session 无历史操作记录)` |
| 不足 10 条 | header 显实际数 `最近 3 条操作` |
| 全 slash 无 llm_turn | 正常显, 不区分 |
| load 失败 | 整段跳过, log warn |

### 6.2 `/history` 命令

#### Syntax

| 形式 | 含义 |
|---|---|
| `/history` | 默认 limit=30, type=all |
| `/history --limit N` | 显最近 N 条, 上限 200 |
| `/history --type slash` | 仅 slash |
| `/history --type llm_turn` | 仅 llm_turn |
| `/history --type slash llm_turn` | 多选 = 等价无 --type (允许显式) |
| `/history --all` | = --limit 200 快捷 |

**默认 30 而非 10**: banner 已显 10, `/history` 是 "想看更多" 的入口, 默认 30 拉开梯度.
200 上限保守, 防超长 session 卡 terminal.

#### 输出样例 (默认 30, 假设当前共 47 条)

```
本 session 共 47 条历史记录, 显示最近 30 条 (旧 → 新):

[2026-05-25 13:55:12] /compress
  概要: +4 L1 / +12 边

[2026-05-25 13:58:34] /run
  概要: +12 L2 / +37 边

...(中略)...

[2026-05-25 14:20:45] /predict
  假设: 假设 LeCun 提出的 JEPA (Joint Embedding Predictive Architecture) 真正解决了
        c_001 结构先验内化深度 + c_004 组合生成能力涌现边界 这两个核心 bottleneck —
        即模型在 latent space 学到了 abstract causal structure, 而不再依赖 pixel-level
        statistical pattern. 请预测在视频物理预测 / 机器人零样本操控 / 物理直觉测试 /
        守恒律涌现 / 组合泛化测试 这些方向, JEPA 架构会显示哪些可被实验证伪的具体
        phenomena.
  概要: +1 L1 / +5 现象 / +12 边

[2026-05-25 14:27:09] 你: c_005 跟 c_001 c_004 之间的因果关系强度可信吗?
                 Claude: c_005 → c_001/c_004 的两条 manifests_as 关系当前 confidence
                 0.78/0.82, 是经过 /rescore 跑过 LLM 推理 mini-batch 的结果...

(输入 /history --type slash 仅看命令, --type llm_turn 仅看对话, --limit N 调数量)
```

**vs banner 差异**:
- `/history` 完整显 intervention (不截 80 字)
- `/history` 完整显 user_input + assistant_text (不截 60 字)
- 每 entry 空行分隔

#### Error case

| 输入 | 响应 |
|---|---|
| `/history --limit 0` | err: "--limit 需 >= 1" |
| `/history --limit -1` | err: 同上 |
| `/history --limit abc` | err: "--limit 需为 1-200 整数" |
| `/history --limit 500` | err: "--limit 上限 200, 当前 500" |
| `/history --type foo` | err: "--type 取值 slash / llm_turn (可多选, 空格分隔)" |
| `/history extra-positional` | err: "/history 不接位置参数, 用 --limit / --type" |
| `/history --limit 5 --type llm_turn` (只 3 条) | 显 3 + footer "实际 3 条 (filter 后)" |

### 6.3 文案 single source — `chat_copy.py` 新增常量

```python
COMMAND_DESCRIPTIONS["history"] = "查看本 session 操作历史 (默认最近 30 条)"

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

err_history_limit_range = lambda v: f"错误: --limit 上限 200, 当前传入 {v}."
err_history_limit_type = lambda: "错误: --limit 需为 1-200 整数."
err_history_type_invalid = lambda v: f"错误: --type 取值为 slash / llm_turn (可多选, 空格分隔). 收到: {v!r}"
err_history_positional = lambda: "错误: /history 不接位置参数, 用 --limit / --type."
```

`/help` 输出 "session 管理" 组加 1 行 `/history — 查看本 session 操作历史`.

---

## 7. Error Handling

### 降级原则

> **history 是 nice-to-have, 任何环节失败都不能 propagate 影响 chat 主流程.**

### 1. 写入失败 (append_repl_history)

Wrapper 包 try/except, log warn, 吞掉.

### 2. 读取失败 (load_repl_history)

- 文件不存在 (老 session): return `[]`, 不警告
- 单行 JSON 损坏: silent skip + log warn (rate-limited, 最多 5 + 1 summary)
- 整文件不可读 (权限/IOError): try/except 包 read_text, log warn, return `[]`
- 文件大: MVP 不做 streaming, 全 load (假定单 session < 几千 entry)

### 3. Snapshot 失败

`_snapshot_graph_safe` 包 try/except 返 None; `_compute_delta` 见 None 返
`"(变化未知)"`.

### 4. Handler 异常 (最关键)

- **写带 error 字段的 entry, 再 raise**: 用户重启后 banner 看到 "失败" 比缺记录有价值
- Entry schema 加 optional `error` 字段
- Summary = `"(执行失败: ErrType)"`

### 5. 中断

- **SIGINT (Ctrl+C)**: `KeyboardInterrupt` 不被 wrapper try 接, 正常 propagate, history
  不写 (用户主动放弃)
- **SIGKILL / 断电**: 单 entry < PIPE_BUF 原子写; chat REPL 单线程 async, 无并发竞争
- **LLM 流式打断**: `process_user_turn` 完整结束才 append, 中断不写

### 6. `/history` 参数边界

(见 6.2 Error case 表)

### 7. 老 session 兼容

Resume 时 `load_repl_history` 返 `[]` → banner 显 "无历史". 老操作不回填. 新操作从该
时刻起记录.

### 8. 文件被外部破坏

单行 / 整文件按 case 2 处理. warning rate-limit 防刷屏.

### 9. project_id 切换 / 跨机器同步

不在本 phase 范围, 沿用 Phase 10 既有行为.

---

## 8. Testing 策略

### 8.1 文件分布

```
tests/test_persistence_storage_v2.py   (现有, 加 append/load repl_history 测试)
tests/test_chat_slash_commands.py      (现有, 加 wrapper + _handle_history 测试)
tests/test_chat_session.py             (现有, 加 llm_turn 写入 + ChatEvent.metadata)
tests/test_chat_copy.py                (现有, 加 文案常量测试)
tests/test_chat_repl_history.py        (新建, 端到端 + banner snapshot)
```

### 8.2 TDD Wave 拆分 (按依赖顺序)

| Wave | 模块 | Task 数 | 估时 |
|---|---|---|---|
| 1 | storage append/load + 降级 | 7 | 1 hr |
| 2 | snapshot + delta 纯函数 | 9 | 1.5 hr |
| 3 | dispatcher wrapper | 8 | 2 hr |
| 4 | ChatEvent.metadata + 2 handler intervention | 5 | 1 hr |
| 5 | _handle_history 命令 (含 14 边界) | 14 | 2.5 hr |
| 6 | process_user_turn llm_turn 写入 | 4 | 1 hr |
| 7 | resume banner _render_recent_history | 9 | 1.5 hr |
| 8 | chat_copy 文案常量 | 3 | 0.5 hr |
| 9 | e2e smoke (4 场景) | 4 | 1.5 hr |
| 10 | acceptance doc | 1 | 0.5 hr |
| **合计** | | **64 task / ~10 commit** | **~13 hr** |

Wave 1+2+8 可并行起跑 (zero deps), Wave 3 依赖 1+2, Wave 4-7 依赖 3, Wave 9 依赖
全前置.

### 8.3 测试设计原则

- **Mock 边界**: 用真 `StorageV2` + tmp `EXPLAIN_HOME` env, **不 mock storage 本身**
- **LLM**: 用 `FakeLLM` (项目已有)
- **Console**: `force_terminal=False` 渲染 StringIO
- **ChatState / Graph / Node**: 用真实例 (tests/conftest.py 现有 builder)
- **Async**: pytest asyncio_mode auto (项目已配)
- **新 fixture**: `tmp_explain_home`, `fake_chat_with_history` 加 conftest

### 8.4 详细 Task 列表

(详见 implementation plan, 此处仅列 Wave 拆分骨架. 由 writing-plans skill 展开
逐 task TDD red/green/commit 步骤.)

---

## 9. 估时 + Commit 节奏

- **总时**: ~13 hr (1 全天 + 余量)
- **Commit 数**: ~10 (每 Wave 1 commit, 大 Wave 拆 2)
- **跟项目惯例**: 跟 Phase 14 (15 task / 9 commit) / Phase 15 (19 task / 14 commit) /
  Phase 16 (20 task / 21 commit) 数量级一致

## 10. 后续 Phase 17+ 候选 (不在本 hotfix 范围)

1. **History 反查驱动 theory recompute**: `/history` 数据可喂 cross-session pattern
   detection, 跟 Phase 16 theory formation 协同
2. **Multi-perspective history view**: 按 abstraction level / domain 切片显历史
3. **History 跨 session aggregation**: project-level "全局操作流" — 跟 Phase 10
   lexicon 同维度
4. **Time-based session resume timeline**: 不光显操作, 显图形化时间轴
