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
