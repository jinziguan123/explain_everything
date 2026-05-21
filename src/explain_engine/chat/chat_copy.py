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
    "new":     "重置当前 chat, 回到刚启动的空白状态",
    "resume":  "切换到历史 session",
    "list":    "列出当前项目所有 session",
    "lexicon": "查看跨 session 累积的概念库",

    # 其他
    "budget":  "查看 / 设置 LLM 调用预算",
    "compact": "强制压缩对话记忆",
    "save":    "立即把当前所有状态存盘",
    "migrate": "(一次性) 老 session 文件迁移到新存储格式",

    # 帮助 / 退出
    "help": "看命令列表",
    "quit": "退出 (自动存盘)",
}


HELP_GROUPS_ZH: list[tuple[str, list[str]]] = [
    ("推进 session",              ["compress", "run", "rescore"]),
    ("干预分析 (需先 /compress)", ["predict", "counterfactual"]),
    ("查看状态 (只读)",           ["show", "graph", "check"]),
    ("管理 session",              ["new", "resume", "list", "lexicon"]),
    ("其他",                      ["budget", "compact", "save", "migrate"]),
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
        f"或 /resume 选历史 session."
    )


def err_stage_not_allowed(cmd: str, current_stage: str, allowed: list[str]) -> str:
    current_zh = zh(current_stage)
    allowed_zh = " / ".join(zh(s) for s in allowed)
    return (
        f"/{cmd} 在当前阶段 ({current_zh}) 不能跑 — "
        f"需要阶段为: {allowed_zh}."
    )


STATUS_COMPRESS_PROPOSE = "[bold green]正在归纳模式...[/bold green]"
STATUS_COMPRESS_SCORE   = "[bold green]正在评分候选模式...[/bold green]"
STATUS_LEXICON_FLUSH    = "[bold green]正在存盘到概念库...[/bold green]"
STATUS_RUN              = "[bold green]正在自动推理 (扩展 / 反思 / 衰减)...[/bold green]"
STATUS_PREDICT          = "[bold green]正在预测干预影响...[/bold green]"
STATUS_COUNTERFACTUAL   = "[bold green]正在做反事实分析...[/bold green]"
STATUS_RESCORE          = "[bold green]正在重评因果关系...[/bold green]"

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
