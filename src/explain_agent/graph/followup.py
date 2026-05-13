import asyncio
from datetime import datetime
from uuid import uuid4

from sqlalchemy import text

from explain_agent.llm import LLMClient


FOLLOWUP_SYSTEM = """你是金融归因 agent 的追问回答器。基于已生成的 6 维归因报告
和历史追问，针对用户的新问题给出 150-400 字的回答。

要求:
- 优先基于已有 dimension_reports + citations 中的 evidence 回答, 不要编造新数据。
- 用 [e_xxx] 格式标注引用的 evidence_id。
- 如果用户追问的事件（如"X 政策"、"Y 访华"、"Z 突发新闻"）在已有证据中没有
  直接覆盖, 先尝试基于现有 evidence 推测其可能影响（明确标注"基于现有 6 维证据
  推测"）, 再说明"若需该事件的实时数据/详细分析, 建议用 /new 开新会话"。
- 仅当问题完全跨标的（如用户已问半导体股票却来问"我的健康问题"等非金融场景）
  才直接拒绝并提示 /new。
- 不预测/不推荐操作。

直接输出回答文本。
"""


def _build_followup_prompt(session: dict, history: list[dict], question: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    citations_text = "\n".join(
        f"  - [{c.get('evidence_id')}] ({c.get('source_type')}) "
        f"{(c.get('snippet') or '')[:200]}"
        for c in (session.get("citations") or [])[:20]
    )
    history_text = "\n".join(
        f"Q: {h['question']}\nA: {h['answer']}"
        for h in history[-5:]
    ) or "（无历史追问）"
    dim_reports_text = "\n\n".join(
        f"## {dim_id}\n{report}"
        for dim_id, report in session.get("dimension_reports", {}).items()
    ) or "（无维度报告）"
    return (
        f"当前时间: {now}\n"
        f"会话标的: {session.get('target')}\n"
        f"时间窗: {session.get('time_window')}\n"
        f"市场锚点: {(session.get('market_facts') or {}).get('snippet', '')}\n\n"
        f"=== 原始叙事 ===\n{session.get('narrative', '')}\n\n"
        f"=== 6 维度报告 ===\n{dim_reports_text}\n\n"
        f"=== 引用证据 (top-20) ===\n{citations_text}\n\n"
        f"=== 追问历史 (最近 5 轮) ===\n{history_text}\n\n"
        f"=== 当前新问题 ===\n{question}"
    )


async def run_followup(
    session: dict,
    history: list[dict],
    question: str,
    llm: LLMClient,
    engine,
) -> dict:
    user = _build_followup_prompt(session, history, question)
    try:
        answer = await llm.achat(system=FOLLOWUP_SYSTEM, user=user, max_tokens=2000)
    except Exception as e:
        answer = f"（追问失败：{e!s}。请重试或换问法）"
        return {"answer": answer, "session_id": session["session_id"]}

    asyncio.create_task(_persist_followup(engine, session["session_id"], question, answer))
    return {"answer": answer, "session_id": session["session_id"]}


async def _persist_followup(engine, session_id: str, question: str, answer: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO explain_agent.explain_followup_history
                      (followup_id, session_id, question, answer, created_at)
                    VALUES (:fid, :sid, :q, :a, :ts)
                    """
                ),
                {
                    "fid": f"f_{uuid4().hex[:12]}",
                    "sid": session_id,
                    "q": question,
                    "a": answer,
                    "ts": datetime.now(),
                },
            )
    except Exception:
        pass
