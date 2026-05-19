# Slash `/show` + `/graph` Detail Enhancement Design

**Date**: 2026-05-19
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 9-11 chat REPL (slash 命令架构)
- Fix 3 (commit `7979096`) `_format_node_brief` helper

## 1. Motivation

当前 `/show` 仅输出 graph summary 4 段 (sid / question / stage / counts) + multi-signal acceptance 4 个数字, graph 真实结构 (具体哪些 node、edge、name+desc、字段 quality) 完全黑盒.

User 实测场景:
```
> /show
Session: s_f5ebd3a9
Question: 为什么现在的年轻人消费少了
Stage: bootstrap_pending
Graph: 33 nodes (20 L0 / 13 L1 / 0 L2)
Lifecycle: 0 decayed, 0 stale

Multi-signal acceptance:
  avg_consistency: 0.540
  avg_essentialness: 0.000
  weak_chain_l1s: ['c_007', 'c_010', 'c_013', 'c_006', 'c_009', 'c_012']
  rollout_coverage: 1.000
```

问题:
- 33 nodes 看不到具体内容 (name + desc + confidence + epistemic)
- 0-60 个 edges 看不到 (没 source/target/mechanism)
- `weak_chain_l1s` 只显 ID, user 不知是哪些概念 (跟 Fix 3 同类问题, 但 Fix 3 只覆盖 /predict / /counterfactual)
- 只有 acceptance verdict 数字, 没有 detail 支撑

## 2. Goals

1. `/show` text 输出全展开 graph (按 L0/L1/L2 分组 + edge 按 type 分组), 含核心字段
2. 新增 `/graph` slash, graphviz 渲染 inline 可视化 (terminal 直显 PNG via iTerm2/Kitty/chafa)
3. text 和 visual 互补: text 看字段, visual 看拓扑

## 3. Non-Goals

- 单 node 详情查询 (`/show <id>` mode) — 当前不做, 字段都在 `/show` 全貌里
- 自动重渲染 (graph 变化推送) — 用户主动 `/graph` 即可
- Cross-session graph 渲染 (多 session 跨绘) — Phase 12 theory formation 范畴
- Web UI / 桌面 GUI — terminal-only
- 持久化 PNG 历史 — 每次退出清理

## 4. Design

### 4.1 `/show` text 增强 (改现有 handler)

**输出 layout** (top-to-bottom):
```
=== Session ===
sid:      s_f5ebd3a9
question: 为什么现在的年轻人消费少了
stage:    bootstrap_pending

=== Graph (33 nodes: 20 L0 / 13 L1 / 0 L2; 0 decayed, 0 stale) ===

[L0 Observations] (20)
  p_001 [obs 0.85] 「房价上涨」: 一线城市房价持续上涨,购房压力大...
  p_002 [obs 0.80] 「就业压力」: 互联网行业大规模裁员,应届就业...
  ...

[L1 Concepts] (13) — weak chain: c_006 c_007 c_009 c_010 c_012 c_013
  c_001 [insight 0.78] 「经济不安全感」: 对未来收入预期下降...
  c_006 [insight 0.55] (weak) 「保守消费」: 防御性储蓄上升...
  ...

[L2 Drivers] (0)
  (none — 尚未 expand 出 root driver)

=== Edges (52) ===

manifests_as (29):
  c_001 → p_001 [0.85] 经济不安全感在房价感受层表现为...
  ...

causes (15):
  c_001 → c_007 [0.80] 收入预期降→防御性储蓄上升...
  ...

amplifies (8):
  c_003 → c_001 [0.70] 社交媒体放大焦虑→经济不安全感强化...
  ...

=== Multi-signal acceptance ===
avg_consistency:    0.540
avg_essentialness:  0.000
rollout_coverage:   1.000
weak_chain_l1s (6): c_006 c_007 c_009 c_010 c_012 c_013
```

**Layout 决策**:
- 顺序 Session → Graph (L0/L1/L2 分组) → Edges (按 type 分组) → Multi-signal verdict 末尾, 看完 detail 再看 verdict 更顺
- weak L1 双重曝光: L1 section header 内一次 + Multi-signal 段重复一次. 防 100+ nodes 时 weak 信息被 node tree 淹没
- lifecycle decayed/stale 节点不过滤, 用 `[decayed]` / `[stale]` 标记 (跟 weak 同位)
- 无 emoji / Rich Table; 保持 plain text `str` (符合 `ChatEvent.content: str` 现有契约). 所有 marker 用 ASCII

**Node 行格式**:
```
  {id} [{epi_short} {conf:.2f}] {marker?} 「{name}」: {desc[:60]}{...}?
```
- `epi_short`: `fact` / `obs` (`observation`) / `inf` (`inference`) / `ins` (`insight`) / `spec` (`speculation`)
- `marker` 优先级: `[decayed]` > `[stale]` > `(weak)` > 空. lifecycle 决定 graph 演化, 比 weak (multi-signal 视角) 更刚性
- `desc[:60]` + `...` (沿用 `_format_node_brief` max_desc default)

**Edge 行格式**:
```
  {source_id} → {target_id} [{conf:.2f}] {mechanism[:60]}{...}?
```
- `relation_type` 不显行内 (已被 section header 分组, 减重复)
- source/target 仅 ID, 不展开 name (避免行宽爆炸; 上方 node tree 可查)

**排序**:
- node 在 L group 内按 ID 升序 (deterministic, snapshot test 友好; 不按 confidence 排避免 LLM run 间漂移)
- edge 在 type group 内按 (source_id, target_id) 升序

**边界**:
- 空 graph: Graph section 显 `(empty)`, 各 L section 不输出. Edges section 显 `(no edges)`
- aggregate_acceptance 异常: 保留现有 try/except, multi-signal section fallback 一行 `(aggregate_acceptance failed: ...)`

**Helper 复用**:
- `_format_node_brief(state, nid, max_desc=60)` 已存在 (Fix 3 加), 微调使行格式贴合上面 (加 epi_short + conf + marker)
- 新加 `_format_edge_brief(edge, max_mech=60)` (无 state 依赖)
- 新加 `_format_epi_short(epi: str) -> str` 5→3-4 字映射

### 4.2 `/graph` 新 slash — graphviz inline 渲染

**18 → 19 slash**. 命名理由: visual 与 text 是不同 modality (不是 verbose 程度差异), 独立命名比 subcommand 清晰.

**Pipeline**:
1. Build `graphviz.Digraph` from `chat.state.graph`
2. Render PNG → 临时 session-tmpdir 下 `graph_<sid>_<tick>.png`
3. Inline display, 按顺序试 (terminal capability detect)
4. 输出: header 1 行 + 图 (inline) + footer (path + multi-signal 4 行 verdict)

**临时目录 + 自动清理**:

```python
import tempfile, atexit, shutil

_SESSION_TMPDIR: str | None = None  # process-global

def _get_session_tmpdir() -> str:
    """Lazy init at first /graph call. atexit cleanup."""
    global _SESSION_TMPDIR
    if _SESSION_TMPDIR is None:
        _SESSION_TMPDIR = tempfile.mkdtemp(prefix='explain_graph_')
        atexit.register(shutil.rmtree, _SESSION_TMPDIR, ignore_errors=True)
    return _SESSION_TMPDIR
```

- Lazy init: 不用 /graph 的 session 完全不创目录
- 进程级 tmpdir (非 session 级): 同一 REPL 内 `/new` / `/resume` 多 session 共享, filename 含 sid 区分 (`graph_<sid>_<tick>.png`), 互不覆盖. /quit 第一 session 不应清后续 session 的图 (user 可能 /resume 老 session 后再 /graph)
- atexit.register 覆盖 `/quit` 正常退出 / Ctrl-C / Ctrl-D / SystemExit / 异常退出. 不覆盖 `os._exit` 或 `kill -9` (REPL 不走这两路径)
- 退出后 PNG 路径失效 — 符合用户预期 "磁盘干净"

**Visual encoding**:

| 元素 | 编码 |
|---|---|
| L0 节点 | `shape=box`, `fillcolor=lightblue` |
| L1 节点 | `shape=ellipse`, `fillcolor=lightyellow` |
| L2 节点 | `shape=doubleoctagon`, `fillcolor=lightcoral` |
| 节点 label | `id` \n `「name」` \n `[conf]` (3 行紧凑, 不含 desc — graph 节点放不下, desc 走 `/show`) |
| lifecycle decayed | `style=dashed,filled`, `fillcolor=gray80` (褪色暗示) |
| lifecycle stale | `style=dotted,filled` |
| weak L1 (weak_chain_l1s) | `color=red, penwidth=2` (红边框) |
| lifecycle vs weak 优先级 | lifecycle 视觉效果叠加 weak (decayed+weak 同时显示, 跟 text 一致) |
| edge causes | 实线 (默认) |
| edge amplifies | `penwidth=2.5` (粗实线) |
| edge suppresses | `color=red` |
| edge constrains | `color=blue` |
| edge manifests_as | `style=dashed` |
| edge label | type 3 字缩写 + conf (`cau 0.80` / `man 0.85`); mechanism 不显 |
| rankdir | `TB` (top-down, L2 在上 L0 在下, 符合 "抽象提升" 直觉) |

**Inline render 检测** (按优先级试):
1. iTerm2: `$TERM_PROGRAM == "iTerm.app"` 且 `shutil.which("imgcat")` → `subprocess.run(["imgcat", path])`
2. Kitty / Ghostty: `$KITTY_WINDOW_ID` 或 `$TERM_PROGRAM == "ghostty"` → `subprocess.run(["kitty", "+kitten", "icat", path])`
3. chafa: `shutil.which("chafa")` → `subprocess.run(["chafa", "--size", "100x40", path])`
4. fallback: 输出 `PNG saved: <path>\n(install chafa for inline preview: brew install chafa)`

**输出终端 layout**:
```
/graph tick=5 · 33 nodes 52 edges

<inline image>

PNG: /tmp/explain_graph_xxx/graph_s_f5ebd3a9_5.png

Multi-signal: consistency=0.540 essentialness=0.000 coverage=1.000
weak L1: c_006 c_007 c_009 c_010 c_012 c_013
```

**新依赖**:
- `pyproject.toml`: 加 `graphviz>=0.20` (Python 包, dot binary 轻 wrapper)
- 系统: `brew install graphviz` (必, 提供 `dot` binary), `brew install chafa` (推荐, 通用 fallback)
- First-call 检查 `shutil.which("dot")`, 缺 → 输 `dot binary not found. Install: brew install graphviz`, 不 crash

**边界**:
- 空 graph: 不调 graphviz, 直接输 `(empty graph, nothing to render)`
- dot 缺: 友好 error + brew install 提示, 不 crash session
- chafa size 自适应 terminal width 不做, 固定 `100x40` (后续可加 flag, 当前 YAGNI)

## 5. Test Plan

### 5.1 `/show` text (改现有 + 新加)

`tests/chat/test_slash_show.py` (verify existing or create):
- `test_show_layout_sections`: 输出含 4 个 section header (`=== Session ===`, `=== Graph ===`, `=== Edges ===`, `=== Multi-signal acceptance ===`)
- `test_show_node_format`: node 行 match regex `r"  \w+_\d+ \[\w+ \d+\.\d+\] (\(weak\) |\[stale\] |\[decayed\] )?「.+」: .{0,60}\.{0,3}"`
- `test_show_edge_grouping`: edges section 按 relation_type 分组, group header `(N):` 显式
- `test_show_weak_marker_priority`: 同一节点 lifecycle=stale 且 weak → 显 `[stale]` 不显 `(weak)`
- `test_show_empty_graph`: 0 nodes → Graph section `(empty)`, Edges section `(no edges)`
- `test_show_aggregate_failure`: monkeypatch aggregate_acceptance raise → multi-signal section fallback 一行, 整个 /show 不 crash

### 5.2 `/graph` 新

`tests/chat/test_slash_graph.py` (新建):
- `test_build_digraph`: from state.graph → 验 Digraph node/edge 计数, L0/L1/L2 shape+fillcolor 对
- `test_label_format`: node label = `id\n「name」\n[conf]`
- `test_lifecycle_marker`: decayed 节点 style=dashed gray, stale 节点 dotted, weak L1 红边框
- `test_edge_style`: causes 实线, amplifies penwidth=2.5, manifests_as dashed, suppresses red, constrains blue
- `test_edge_label`: edge label = `cau 0.80` 格式 (3 字 + conf)
- `test_render_iterm` (mock env `TERM_PROGRAM=iTerm.app` + mock `shutil.which`): 验调用 `imgcat`
- `test_render_kitty` (mock env `KITTY_WINDOW_ID`): 验调用 `kitty +kitten icat`
- `test_render_chafa` (mock 前两不可用 + `shutil.which("chafa")`): 验调用 `chafa --size 100x40`
- `test_render_fallback` (mock all unavailable): 输出含 "PNG saved" + chafa install hint
- `test_tmpdir_lazy_init`: 不调 /graph 不创目录; 第一次调创, 第二次复用
- `test_tmpdir_atexit_cleanup`: monkeypatch atexit, 验 register 了 shutil.rmtree on the tmpdir
- `test_empty_graph`: 0 nodes → 输 "(empty graph, nothing to render)", 不调 graphviz
- `test_dot_missing` (mock `shutil.which("dot")` None): 输友好 error, 不 crash

### 5.3 Smoke

- 现有 `/show` 测试 全 pass (改 layout 后 update assertions)
- 18 slash 测全 pass (新 /graph 加进 SLASH_COMMANDS dict, slash registry test 验 19)
- `explain` REPL 跑实际 session, 手测 `/show` 输出 layout 对 + `/graph` 在 iTerm2 渲染 inline (acceptance)

## 6. Backward Compatibility

- `/show` 输出格式 break (旧 4 字段 → 新 layout). 但 `/show` 是 user-facing 交互, 无 downstream 解析依赖. 测试断言更新即可.
- `_format_node_brief` 行格式调 (加 epi_short + conf + marker), `/predict` / `/counterfactual` 已有调用方需 verify (Fix 3 上游) — 估计 visual 上影响 minor, 不破坏功能.
- pyproject.toml 加 `graphviz>=0.20` 是新 dep, `uv sync` 后自动装.
- `dot` binary 缺 → 友好 error, 不 crash 现有 18 slash. 不强制装 graphviz, 不调 /graph 的 user 零影响.

## 7. Effort & Risk

**Effort**: ~400-500 lines code + ~250 lines test. 估 1.5-2 天.
- `/show` text 改: ~150 lines
- `/graph` 加: ~250 lines (含 helper + render + detect)
- Test: ~250 lines

**Risk**:
- 低: text 改是 pure refactor, helper 复用, 影响面小
- 中: graphviz Python wrapper 在 macOS uv 环境装一次未验过, 可能要调依赖
- 中: terminal capability 检测 (iTerm2/Kitty/chafa) 在 user 真实环境有 edge case, 可能要补 fallback
- 低: atexit cleanup 在 Python 标准库稳定, REPL 退出路径已 check 无 os._exit

## 8. Open Questions (none)

设计已对齐, 进 implementation plan.
