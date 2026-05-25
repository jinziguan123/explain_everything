# Phase 16 Acceptance: Theory Formation (跨 session 因果模式 emergent)

**Date**: 2026-05-21

## Manual Smoke Test (13 步, 跑 5 个相关 question session)

> 目标: cold-start → 5 个相关 session → /theories 见至少 1 个 stable/tentative theory →
> /theory <id> 看详情 → reject 一个 → 新 session bootstrap 验 inject 跳 rejected.

### 0. 准备 (干净 project)

```bash
# 先确认当前 project 没有 theory cache 残留 (可选, 想看 cold-start 真状态)
rm -f ~/.explain/projects/<your-proj>/knowledge/theories.json
```

也可 `cd` 到一个新目录跑 ephemeral REPL — `~/.explain/projects/<cwd-hash>/` 自动隔离.

### 1. 启动 chat REPL

```bash
.venv/bin/python -m explain_engine.cli
```

预期: banner 进入 ephemeral chat 模式 (空白状态).

### 2. /theories — 0 session cold-start

```
> /theories
```

预期: `需累积 ≥ 3 个 session 才能形成 theory. 当前: 0/3.`

(threshold = `max(3, len(sessions) // 3)`, ≤ 9 session 都是 3).

### 3. 输自然语言问题 → bootstrap (session 1)

```
> 为什么年轻人储蓄少
```

预期: implicit `/new` → bootstrap → HITL 现象审查 → stage 推进到 `bootstrap_done` 等价
(具体 chat 内 stage 名见 chat_state). 此时 bootstrap LLM 调用 prompt **不含**
`## 跨 session 已发现的稳定因果模式` 段 (theories=[]).

### 4. /compress + /run → done session 1

```
> /compress
> /run
```

预期: 跟 Phase 14/15 一致 (中文 status / next-step hint / 完成 msg). 完成后 lexicon
被写入 (Phase 10 行为 + Phase 13 embedding).

### 5. 重复 step 3-4 跑 session 2

```
> /new
> 为什么年轻人不结婚
> /compress
> /run
```

预期: bootstrap 用 lexicon Top-K (Phase 10) 但 theories 段仍空 (1 session < cold start
threshold = 3).

### 6. 重复 step 3-4 跑 session 3

```
> /new
> 为什么年轻人躺平
> /compress
> /run
```

### 7. /theories — 应见 cold-start threshold 达到

```
> /theories
```

预期: `n_sessions = 3 ≥ cold_start_threshold (3)`, 进真 recompute path (status
spinner `正在分析跨 session 模式...` 闪一下). 之后两种可能:

- **若 motif 形成** (3 session 共 theme + 共 motif freq ≥ 3) →
  table `跨 session 因果模式 (stable: 0, tentative: N, rejected: 0)`. 但 stable=0
  因 `window_size=5` 未到 → 默认只显 stable 即 empty (走 `msg_theories_no_motif_found`).
- **若 motif 未形成** (3 session theme/motif 不重合) → `已分析 3 个 session, 未发现重复
  出现的因果模式. 跑更多 session 试试.`

加 `--all` 强制显 tentative:

```
> /theories --all
```

若 tentative 存在, 会看 table 含至少 1 行 `待观察` 状态.

### 8. 跑 session 4 + session 5

```
> /new
> 为什么年轻人不生育
> /compress
> /run

> /new
> 为什么年轻人不投资
> /compress
> /run
```

(5 个 question 均围绕"年轻人对未来不确定 → 行为退缩"主题, 期望 5 个 session 间出现
共享 theme + 共享 motif).

### 9. /theories — 应见至少 1 个 stable/tentative theory 形成

```
> /theories
```

预期 (best-case): table 含 1-2 行, 至少 1 个 `已稳定` 状态. 模式自然语言描述类似:

```
跨 session 因果模式 (stable: 1, tentative: 2, rejected: 0)
ID         类型      模式                                    覆盖 session  预测准确度  状态
t_abc123   因果链    不确定性 → 风险规避 → 行为退缩              4/5         80%        已稳定
t_def456   星型      经济压力 → (储蓄/婚育/投资)                  3/5         67%        待观察
```

字段含义 (跟 chat slash table 一致):

- **类型** 中文化: `因果链 / 星型 / 环路` (不见 `chain / star / cycle`)
- **状态** 中文化: `已稳定 / 待观察 / (已拒绝)` (不见 `stable / tentative / rejected`)
- **预测准确度**: leave-one-session-out predictive_power 百分比 (JEPA 启示 a)

若仍 empty, 看 `--all` + 调更密相关 question (e.g. 多跑 1-2 个).

### 10. /theory <id> — 显详情

```
> /theory t_abc123
```

预期 (多 section, lines join):

```
=== Theory t_abc123 ===
模式: 不确定性 → 风险规避 → 行为退缩
类型: 因果链
状态: 已稳定
覆盖 session: 4/5
预测准确度: 80%
首次发现: s_xxxxxxxx
最近出现: s_yyyyyyyy

=== 涉及的 theme ===
  · 不确定性 (含 3 个变量: v_xxx, v_yyy, v_zzz)
  · 行为退缩 (含 2 个变量: v_aaa, v_bbb)

=== 因果结构 ===
  v_xxx 导致 v_yyy
  v_yyy 体现为 v_aaa

=== 支撑 session ===
  · s_xxxxxxxx
  · s_yyyyyyyy
  · ...
```

- 因果结构 relation_type 中文化: `导致 / 体现为 / 抑制 / 约束 / 放大` (zh() helper).
- theme name 取 cluster centroid 最近 member.name (e.g. `不确定性`).

### 11. /theory <id> reject — 拒绝, 再 /theories 应见 (已拒绝) mark

```
> /theory t_abc123 reject
```

预期: `已拒绝 theory t_abc123, 后续不再用于 bootstrap inject.`

```
> /theories --all
```

预期: table 不再含 t_abc123 (visible 已 filter rejected). 但 reject 计数 +1:
`(stable: 0, tentative: 2, rejected: 1)`.

幂等: 再跑一次 `/theory t_abc123 reject` 也返同 msg (不报错).

### 12. /new + 输新问题 — bootstrap LLM prompt 应含 stable theory 段

```
> /new
> 为什么年轻人对买房没兴趣
```

预期: bootstrap 走 `ephemeral.promote_to_persistent` 内 `get_active_theories` 取
stable theories (filter rejected), 注入 `propose_phenomena` prompt. 因 t_abc123
已 reject, prompt 段只含其他 stable theory.

**验证手段** (任选其一):

1. LLM 生成的现象列表是否含 theory 中术语 (e.g. `风险规避` / `不确定性`) — 软引导
   实际生效.
2. 看 chat HTTP log (`ctrl+o`) 翻 propose_phenomena 那次 request body, 应在 user
   message 末尾找到:

   ```
   ## 跨 session 已发现的稳定因果模式 (仅供参考, 不必强求新问题 match)

   你过去分析的多个 session 中, 以下因果模式反复出现:

   1. <theory.natural_language_summary> (出现 N session, 预测准确度 X%)
   ...

   **注意**:
   - 这些是历史 pattern, 新问题可能跟它们相关也可能完全无关
   - 若现象自然映射到上述模式, 可复用其涉及的术语 (e.g. "风险规避")
   - 不要强行套, 若新问题领域不同就忽略
   ```

3. 若 stable theories 全被 reject (本场景 t_abc123 是唯一 stable), prompt 段应**为空**
   (backward compat, 跟 theories=[] 一致).

### 13. /quit 验中文 farewell

```
> /quit
```

预期: `再见, session 已存盘.` (Phase 15 中文化).

## 通过标准

- 5 session 后 `/theories` 至少有 1-2 个 stable 或 tentative theory.
- `/theories /theory <id> /theory <id> reject` 全 work, 无 traceback.
- bootstrap inject: stable theory 段进 LLM prompt, rejected 被过滤.
- 输出全中文 jargon-free: `因果链 / 星型 / 环路` (motif_type), `已稳定 / 待观察 / 已拒绝`
  (stability_status), `导致 / 体现为` (relation_type). 不见 raw `chain / star / cycle /
  stable / tentative / causes / manifests_as`.
- 退出 farewell 中文 (Phase 15 行为).

## 已知 limit + 待 follow-up

- BGE-M3 加载首次 ~3-5s (singleton cached), 用户感受 /theories 首次 spinner 慢.
- Cold start motif freq=cold_start_threshold (3) 严格 — 若 5 session 但都不同 theme,
  theory 数 = 0. 真实场景需 user 跑相关 question.
- gSpan MVP 简化 canonical labeling — 高度对称 motif 可能漏 dedup (≤5 node + label
  异质 case 不触发).
- predictive_power 当前 encode motif.nodes 字面 global_id (而非 canonical name),
  后续 polish 改 lookup lexicon.
- chat /theory <id> 显详情 但不支持 chat 内 edit theory (plan §3 non-goal, 仅 reject).

## 等价 CLI 验证

不进 chat REPL 也可直接 cli:

```bash
.venv/bin/python -m explain_engine.cli theories             # stable 默
.venv/bin/python -m explain_engine.cli theories --all       # 含 tentative
.venv/bin/python -m explain_engine.cli theories --force     # 强制重算 cache
.venv/bin/python -m explain_engine.cli theories --project <id> --limit 5
```

输出格式跟 chat slash 一致 (复用同 type_zh_map + table column).
