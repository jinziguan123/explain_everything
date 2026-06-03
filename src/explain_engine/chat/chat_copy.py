"""Phase 15: chat REPL user-facing 中文文案集中点.

所有 chat REPL 路径 (slash_commands / slash_stage_rules / cli._render_event /
hitl/cli_interactive) 引此 module 的常量 / 模板, 不再硬编中文/英文.
单一改文案 source — 改一处全场动.
"""

from __future__ import annotations

TERMS_MAP: dict[str, str] = {
    # ── Graph 节点层次 ──
    "L0": "现象",
    "L1": "归纳出的模式",
    "L2": "深层原因",
    "abstraction": "归纳",
    "driver": "深层原因",
    "observation": "现象",

    # ── Graph 结构 ──
    "graph": "因果图",
    "node": "节点",
    "edge": "因果关系",
    "weak_chain": "薄弱的因果链",
    "manifests_as": "体现为",
    "causes": "导致",

    # ── Runtime ops ──
    "propagation": "影响传导",
    "expansion": "扩展",
    "reflection": "回顾反思",
    "decay": "衰减",
    "reasoning loop": "推理循环",
    "tick": "推理步",

    # ── Cross-session ──
    "lexicon": "跨 session 概念库",
    "canonical_mechanism": "标准化的机制描述",
    "global_id": "全局 ID",

    # ── Multi-signal acceptance ──
    "multi-signal": "多信号",
    "consistency": "一致性",
    "essentialness": "本质重要性",
    "rollout_coverage": "覆盖率",
    "acceptance report": "接受度评估",

    # ── Stage (session.meta.stage 4 个值) ──
    "bootstrap_pending": "等待启动",
    "insight_pending":   "等待审查",
    "done":              "已归纳",
    "converged":         "已推理",

    # ── 引擎 ops ──
    "propose_candidates": "提候选",
    "score_all": "评分",
    "HITL": "人工审查",
    "persist": "存盘",
}


def zh(term: str) -> str:
    """把英文/技术词翻成中文直观短语. 找不到返原词 (defensive)."""
    return TERMS_MAP.get(term, term)


COMMAND_DESCRIPTIONS: dict[str, str] = {
    # 推进 session
    "compress": "把多个现象归纳成模式 (建 session 后第一步)",
    "run":      "自动推理找深层原因 (compress 之后做)",
    "rescore":  "重新评估所有因果关系的可信度",

    # 干预分析
    "predict":         "预测某假设干预会带来什么下游影响",
    "counterfactual":  "反事实分析: 如果不发生某事, 系统会怎么样",
    "cf":              "(等同 /counterfactual)",

    # 查看状态
    "show":  "显示当前 session 的因果图 + 接受度评估",
    "graph": "渲染因果图的可视化 (需安装 graphviz)",
    "check": "查看接受度评估报告 (跟 /show 信息侧重不同)",

    # 管理 session
    "deepen":   "触发深度建模 (/deepen [问题], 不带参取最近 user 输入)",
    "new":      "重置当前 chat, 回到刚启动的空白状态",
    "resume":   "切换到历史 session (/resume <sid>; 先 /list 看 sid)",
    "list":     "列出当前项目所有 session",
    "delete":   "删除某个 session (默认二次确认; --force 跳过)",
    "lexicon":  "查看跨 session 累积的概念库",
    "theories": "查看跨 session 发现的稳定因果模式",
    "theory":   "看某 theory 详情 / 拒绝它 (/theory <id> [reject])",
    "history":  "查看本 session 操作历史 (默认最近 30 条)",

    # 其他
    "budget":   "查看 / 设置 LLM 调用预算",
    "compact":  "强制压缩对话记忆",
    "save":     "立即把当前所有状态存盘",
    "migrate":  "(一次性) 老 session 文件迁移到新存储格式",
    "thinking": "切 thinking 段折叠/展开 (/thinking on|off, 跟 Ctrl+O 等价)",
    "llm":      "管理 LLM 配置 (base_url/api_key/model, 多套切换)",

    # 帮助 / 退出
    "help": "看命令列表",
    "quit": "退出 (自动存盘)",
}


HELP_GROUPS_ZH: list[tuple[str, list[str]]] = [
    ("推进 session",              ["compress", "run", "rescore"]),
    ("干预分析 (需先 /compress)", ["predict", "counterfactual"]),
    ("查看状态 (只读)",           ["show", "graph", "check"]),
    ("管理 session",              ["deepen", "new", "resume", "list", "delete", "lexicon", "theories", "theory", "history"]),
    ("其他",                      ["budget", "compact", "save", "migrate", "thinking", "llm"]),
    ("帮助 / 退出",               ["help", "quit"]),
]


STOP_REASON_MAP: dict[str, str] = {
    # runtime/stop.py 实际 enum (Phase 5/7/8)
    "budget_exhausted":         "预算耗尽",
    "no_gain_for_3_ticks":      "已停 3 步无新发现 (已收敛)",
    "reflection_signaled_stop": "回顾反思后判定收敛",
    "no_frontier_remaining":    "已收敛 (无更多可推进点)",
    # design doc 假设 reasons (兼容已记入 transcript 的旧值 / 上层封装)
    "converged":            "已收敛 (无更多可推进点)",
    "no_meaningful_action": "无更多可推进点",
    "max_ticks":            "达到最大推理步数",
}


def msg_compress_done(
    n_candidates: int,
    n_to_lexicon: int,
    dedup_reused: int | None = None,
    dedup_new: int | None = None,
) -> str:
    """归纳完成消息. dedup_* 同时给时附"复用/全新"统计一行."""
    base = f"归纳完成: 加了 {n_candidates} 个模式, 其中 {n_to_lexicon} 个写入概念库."
    if dedup_reused is not None and dedup_new is not None:
        base += (
            f"\n  · 其中 {dedup_reused} 个与已有模式相似 (跨 session 复用), "
            f"{dedup_new} 个全新."
        )
    return base


def msg_run_done(stop_reason: str, tick: int) -> str:
    reason_zh = STOP_REASON_MAP.get(stop_reason, stop_reason)
    return f"推理完成: 在第 {tick} 步停止 (原因: {reason_zh})."


def msg_rescore_done(n_edges: int, avg_conf: float) -> str:
    return f"重评完成: {n_edges} 条因果关系, 平均可信度 {avg_conf:.2f}. 已存盘."


def msg_save_done(sid: str) -> str:
    return f"已存盘 session {sid} (因果图 + 对话状态 + 转录)."


def msg_resume_already(sid: str) -> str:
    return f"已在 session {sid}, 不切换."


def msg_resume_switching(sid: str) -> str:
    return f"切换到 session {sid}..."


def err_failed(cmd: str, exc: Exception) -> str:
    return f"/{cmd} 失败: {type(exc).__name__}: {exc}"


def err_no_llm(cmd: str) -> str:
    return f"/{cmd} 需要 LLM (启动时没配置)."


def err_ephemeral_reject(cmd: str) -> str:
    return (
        f"/{cmd} 需要先建 session — 输入一个问题让 chat 建 session, "
        f"或 /list 看历史 session 后 /resume <sid> 切."
    )


def err_stage_not_allowed(cmd: str, current_stage: str, allowed: list[str]) -> str:
    current_zh = zh(current_stage)
    allowed_zh = " / ".join(zh(s) for s in allowed)
    return (
        f"/{cmd} 在当前阶段 ({current_zh}) 不能跑 — "
        f"需要阶段为: {allowed_zh}."
    )


def msg_theories_cold_start(current: int, needed: int) -> str:
    return f"需累积 ≥ {needed} 个 session 才能形成 theory. 当前: {current}/{needed}."


def msg_theories_no_motif_found(n_sessions: int) -> str:
    return f"已分析 {n_sessions} 个 session, 未发现重复出现的因果模式. 跑更多 session 试试."


def msg_theory_rejected(theory_id: str) -> str:
    return f"已拒绝 theory {theory_id}, 后续不再用于 bootstrap inject."


def err_theory_not_found(theory_id: str) -> str:
    return (
        f"theory {theory_id} 不存在, 可能 cache 已 invalidate. "
        f"先跑 /theories 看当前 list."
    )


STATUS_COMPRESS_PROPOSE = "[bold green]正在归纳模式...[/bold green]"
STATUS_COMPRESS_SCORE   = "[bold green]正在评分候选模式...[/bold green]"
STATUS_LEXICON_FLUSH    = "[bold green]正在存盘到概念库...[/bold green]"
STATUS_RUN              = "[bold green]正在自动推理 (扩展 / 反思 / 衰减)...[/bold green]"
STATUS_PREDICT          = "[bold green]正在预测干预影响...[/bold green]"
STATUS_COUNTERFACTUAL   = "[bold green]正在做反事实分析...[/bold green]"
STATUS_RESCORE          = "[bold green]正在重评因果关系...[/bold green]"
STATUS_THEORIES_COMPUTE = "[bold green]正在分析跨 session 模式...[/bold green]"

# Phase 19: spinner labels for status_start ChatEvent type.
STATUS_THINKING         = "思考中..."
STATUS_DEEPEN_CLASSIFY  = "启动深度建模 — classify 中..."

# Phase 20.3: agent 自主调工具时 TUI 展示的中文友好标签 (ToolUseEvent.tool_name
# → label). agent loop (query_loop) 里 LLM 自己决定调 expand/compress/... 时,
# 让用户看到"在干什么 + 没卡死". 未列名的 tool fallback 用原始 tool_name.
TOOL_DISPLAY_LABELS: dict[str, str] = {
    "expand":          "扩展因果模型",
    "compress":        "归纳模式 (compress)",
    "check":           "检查模型一致性",
    "predict":         "预测干预影响",
    "counterfactual":  "做反事实分析",
    "add_observation": "记录观察",
    "read_node":       "读取节点",
}


def tool_display_label(tool_name: str) -> str:
    """ToolUseEvent.tool_name → 中文友好标签 (未知名 fallback 原始名)."""
    return TOOL_DISPLAY_LABELS.get(tool_name, tool_name or "工具")

INFO_INSIGHT_PENDING_RESUME = "[dim](检测到中途取消, 跳过 LLM 直接进入审查)[/dim]"
INFO_MID_STAGE_SAVED        = "[dim](中间状态已保存, 取消审查可下次重入跳过 LLM)[/dim]"


HINTS_BY_KEY: dict[str, str] = {
    "need_promote_first": (
        "session 还没启动 — 输入一个问题让 chat 建 session, 然后再 /compress."
    ),
    "need_compress_first": (
        "需要先 /compress 把现象归纳成模式, 才能跑这个命令."
    ),
    "after_compress": (
        "▸ 接下来可选:\n"
        "  /run — 自动推理找深层原因 (推荐)\n"
        "  /predict <假设> — 预测某干预的下游影响\n"
        "  /counterfactual <假设> — 反事实分析"
    ),
    "after_run": (
        "▸ session 已完整推理. 接下来可选:\n"
        "  /predict <假设> — 预测干预影响\n"
        "  /counterfactual <假设> — 反事实分析\n"
        "  /show — 看完整因果图"
    ),
    "after_inference": (
        "▸ 可继续 /predict 或 /counterfactual 探索, /show 看因果图更新."
    ),
    "after_rescore": (
        "▸ 因果关系可信度已重评. /show 看变化, /run 重跑推理."
    ),
}


# ── Phase 16.2: REPL History Persistence — Banner 文案 ──

BANNER_HISTORY_HEADER = "─── 最近 {n} 条操作 (旧 → 新) ───"
BANNER_HISTORY_EMPTY = "(本 session 无历史操作记录)"
BANNER_HISTORY_FOOTER = "输 /history 看完整历史, /help 看所有命令"


# ── Phase 16.2: /history 命令文案 ──

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


# ── Phase 17.2 Feature C: /delete + cli delete 中文文案 ──

STATUS_DELETE_CONFIRM = "确认删 {sid}?"


def msg_delete_done(sid: str) -> str:
    """成功删 session 后给用户看的绿字消息."""
    return f"已删 session {sid}"


def err_delete_not_found(sid: str) -> str:
    """cli / slash 抓 FileNotFoundError 后展示."""
    return f"session {sid} 不存在"


def err_delete_active(sid: str) -> str:
    """slash 拒绝删当前活动 session, 给出可执行动作建议."""
    return (
        f"{sid} 是当前活动 session, 请先 /resume <other_sid> 切到别的 session "
        f"或 /new 重置后再删"
    )


# ── Phase 18: /deepen 中文文案 ──


def err_deepen_no_question() -> str:
    """ephemeral 状态下 /deepen 不带参且 transcript 空 → 用法提示."""
    return "用法: /deepen <问题>  (或先 chat 一句, 再 /deepen 不带参取最近 user 输入)"


def err_deepen_already_promoted(current_question: str) -> str:
    """已 promote 的 ChatSession 内再 /deepen → 拒绝 + 提示 /new."""
    return (
        f"本 session 已 /deepen 过 (建模主题: {current_question}). "
        f"想换主题请用 /new 开新 session."
    )


def msg_deepen_promote_start(question: str) -> str:
    """/deepen 触发 promote 时的状态消息."""
    return f"启动深度建模 (主题: {question})..."


# ── Phase 19 Wave 4: /thinking on|off 文案 ──


def msg_thinking_on() -> str:
    """/thinking on 成功消息. 显 thinking Collapsible 展开."""
    return "thinking 段已开启 (展开). Ctrl+O 也可切."


def msg_thinking_off() -> str:
    """/thinking off 成功消息. 折叠所有 thinking Collapsible."""
    return "thinking 段已关闭 (折叠). Ctrl+O 也可切."


def err_thinking_usage() -> str:
    """/thinking 无参 / 错参用法提示."""
    return "用法: /thinking on  或  /thinking off  (跟 Ctrl+O 等价)"
