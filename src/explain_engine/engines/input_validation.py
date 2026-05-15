"""Phase 8 Wave 3: Input validation engine — 入口 question vs L0 对齐校验.

design §6.2 + §9.4 可证伪性: 系统必须能说"无法回答这个 question",
否则会神学化.

API:
  validate(question, l0_nodes, llm) → InputAlignmentReport
  MIN_OVERLAP_SCORE: 阈值常量 (cli.py 用作 fail-fast 判断)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from explain_engine.engines._llm_retry import call_with_retry
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.nodes import VariableNode

MIN_OVERLAP_SCORE: int = 2
"""overlap_score < 此值 (即 0 或 1) 触发 fail-fast.

阈值保守留 LLM 缓冲. 调优在 acceptance 阶段 (Wave 5).
"""


class InputAlignmentReport(BaseModel):
    """LLM 结构化批判结果."""

    question_subject: str = Field(min_length=1)
    """LLM 识别出的 question 核心主体."""

    observation_subjects: list[str] = Field(default_factory=list)
    """每条 L0 observation 的核心主体列表."""

    overlap_score: int = Field(ge=0, le=5)
    """Question 主体与 observation 主体重叠度: 0=完全无关, 5=高度匹配."""

    falsifiable_reason: str = Field(min_length=1)
    """LLM 给的明确理由 (无论 score 高低都给).
    用于 (a) fail-fast 时给用户解释; (b) 调试 / acceptance review.
    """


async def validate(
    question: str,
    l0_nodes: list[VariableNode],
    llm: LLMClient,
) -> InputAlignmentReport:
    """Wave 3: 入口 input validation.

    哲学锚点 §9.4 + §4.2: 校验 question 与 observations 是否在同一段"历史".

    Args:
        question: root question 文本.
        l0_nodes: 当前 graph 的 L0 nodes.
        llm: LLM client.

    Returns:
        InputAlignmentReport (LLM 结构化判断).

    Raises:
        SchemaValidationError: LLM retry 1 次仍失败.

    Note:
        本函数只返 report, 不抛 InsufficientObservationsError.
        是否 fail-fast 由调用方 (cli.py) 根据 --no-input-check flag + 阈值决定.
    """
    prompt = load_prompt("input_validation")
    l0_table = _render_l0_table(l0_nodes)

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=question,
                l0_table=l0_table,
            ),
        ),
    ]

    return await call_with_retry(
        llm, messages, InputAlignmentReport,
        error_prefix="input_validation 输出 schema 不合规",
    )


def _render_l0_table(l0_nodes: list[VariableNode]) -> str:
    if not l0_nodes:
        return "(none)"
    lines = [f"- {n.id}: {n.name} — {n.description}" for n in l0_nodes]
    return "\n".join(lines)
