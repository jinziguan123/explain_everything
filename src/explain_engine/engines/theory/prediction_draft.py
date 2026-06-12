"""预测自动起草 — "机器提案、人签字" (设计预期-修正版 §七.2 的自动化补全)。

对没有任何台账记录的活跃理论, 由 light LLM 起草 ≤2 条可检验预测并
自动登记 (origin="llm")。结算永远留给人 (retrodiction 除外, 见
ledger.settle_retrodictions)。

触发点:
- explain run 收敛 + 自动接地之后 (best-effort, 失败不阻断)
- explain prediction-draft 手动触发
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from explain_engine.engines._llm_retry import call_with_retry
from explain_engine.engines.theory.ledger import (
    Prediction,
    add_prediction,
    load_ledger,
    stats_by_theory,
)
from explain_engine.engines.theory.theory import Theory
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.prompts._loader import load_prompt

logger = logging.getLogger(__name__)

MAX_DRAFTS_PER_THEORY: int = 2


class _DraftItem(BaseModel):
    assertion: str = Field(min_length=10)
    method: Literal["retrodiction", "search", "time_window"] = "search"
    deadline: str | None = None


class _DraftOutput(BaseModel):
    predictions: list[_DraftItem]


def _valid_deadline(deadline: str | None) -> bool:
    if deadline is None:
        return True
    try:
        datetime.strptime(deadline, "%Y-%m-%d")
        return True
    except ValueError:
        return False


async def draft_for_theory(
    theory: Theory,
    llm: LLMClient,
    today: str | None = None,
) -> list[_DraftItem]:
    """对单个理论起草 ≤2 条预测 (1 次 LLM call)。不落盘。

    LLM 输出的非法项 (time_window 缺 deadline / 日期格式错) 直接丢弃 —
    宁缺毋滥, 台账里不能有不可结算的记录。
    """
    prompt = load_prompt("prediction_draft")
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            summary=theory.natural_language_summary,
            motif_type=theory.motif_type,
            nodes=", ".join(theory.node_ids),
            n_support=len(theory.supporting_sessions),
            today=today,
        )),
    ]
    out: _DraftOutput = await call_with_retry(
        llm, messages, _DraftOutput,
        error_prefix="prediction_draft 输出不合规",
    )
    valid = [
        item for item in out.predictions
        if _valid_deadline(item.deadline)
        and not (item.method == "time_window" and item.deadline is None)
    ]
    return valid[:MAX_DRAFTS_PER_THEORY]


async def auto_draft_predictions(
    storage,
    llm: LLMClient,
    today: str | None = None,
) -> list[Prediction]:
    """对所有"无台账记录的活跃理论"起草并自动登记预测 (origin=llm)。

    范围: stable + tentative, 排除 rejected 与已有任何预测的理论 —
    每个理论只机器提案一次, 起草过(哪怕被结算光了)就不再打扰。
    单理论起草失败跳过, 不中断。

    Returns:
        本次新登记的 Prediction 列表。
    """
    from explain_engine.engines.theory.cache import get_active_theories

    cache = get_active_theories(storage, embedder=None)
    stats = stats_by_theory(load_ledger(storage))
    candidates = [
        t for t in [*cache.stable_theories, *cache.tentative_theories]
        if t.id not in cache.rejected_theory_ids
        and stats.get(t.id) is None  # 从未有过任何台账记录
    ]
    if not candidates:
        return []

    registered: list[Prediction] = []
    for theory in candidates:
        try:
            items = await draft_for_theory(theory, llm, today=today)
        except Exception as exc:
            logger.warning(
                "prediction_draft: %s 起草失败 (%s), 跳过",
                theory.id, type(exc).__name__,
            )
            continue
        for item in items:
            try:
                registered.append(add_prediction(
                    storage, theory.id, item.assertion,
                    method=item.method, deadline=item.deadline, origin="llm",
                ))
            except ValueError:
                continue  # 防御: 非法项已在 draft_for_theory 过滤
        if items:
            logger.info(
                "prediction_draft: %s 起草 %d 条预测", theory.id, len(items),
            )
    return registered
