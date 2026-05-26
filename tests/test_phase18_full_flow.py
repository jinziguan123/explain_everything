"""Phase 18 集成验 — Task 19: ephemeral chat 2 轮 → /deepen 不带参 → promote → 8 phenomena.

end-to-end:
1. 启动 EphemeralChatSession (llm 注入)
2. handle_user_input 2 轮 → transcript 累 4 条 msg
3. /deepen 不带参 → handler 取 transcript 末 user msg 作 question
4. promote_to_persistent 跑 (mock bootstrap 返 8 phenomena)
5. 持久 session 含 8 phenomena, slash_deepen_promoted event metadata.sid 有效
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_full_flow_ephemeral_chat_then_deepen(tmp_path, monkeypatch):
    """Phase 18 end-to-end: chat 2 轮 → /deepen 不带参 → promote → 8 phenomena."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_phase18_full")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
    from explain_engine.persistence.session import SessionStore
    from explain_engine.persistence.storage_v2 import StorageV2
    from explain_engine.schema.nodes import VariableNode

    storage = StorageV2()

    # ── Step 1: mock LLM ──
    # chat 走 .chat() 返 .text; bootstrap_phenomena 我们整体 stub 不真调 LLM
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=[
        MagicMock(text="烧水沸腾是因为水的饱和蒸汽压等于大气压"),
        MagicMock(text="更详细: 加热使水分子动能上升, 蒸发率上升, ..."),
    ])

    # bootstrap_phenomena: 返 8 个 phenomena (Wave 1 stub HITL 全 accept)
    async def fake_bootstrap(question, llm, **kwargs):
        return [
            VariableNode(
                id=f"p_{i:03d}",
                name=f"phenomenon_{i}",
                description=f"desc {i}",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
            for i in range(8)
        ]

    async def fake_review(phenomena, input_provider, console=None):
        return phenomena  # accept all

    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
    )
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
    )

    # ── Step 2: 启动 ephemeral (llm 注入跟 repl_entry 一致) ──
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)

    # ── Step 3: chat 2 轮 ──
    async for _ in ephemeral.handle_user_input("为什么烧水能沸", llm):
        pass
    async for _ in ephemeral.handle_user_input("能再说说蒸汽压吗", llm):
        pass

    assert len(ephemeral.transcript) == 4
    assert ephemeral.transcript[-1]["role"] == "assistant"
    assert ephemeral.transcript[-2]["role"] == "user"
    assert ephemeral.transcript[-2]["content"] == "能再说说蒸汽压吗"

    # ── Step 4: /deepen 不带参 → 取 transcript 末 user msg ──
    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, [])

    # 验 promote 已触发 (slash_deepen_promoted event)
    promoted = [e for e in events if e.type == "slash_deepen_promoted"]
    assert len(promoted) == 1, f"未触发 promote, events={[e.type for e in events]}"
    assert promoted[0].metadata is not None
    new_sid = promoted[0].metadata["sid"]
    assert new_sid.startswith("s_"), f"sid format invalid: {new_sid}"

    # ── Step 5: 验持久 session 含 8 phenomena ──
    sess = SessionStore().load(new_sid)
    assert len(sess.state.graph.nodes) == 8
    # question 是 transcript 末 user msg (不带参 fallback)
    assert sess.meta.question == "能再说说蒸汽压吗"


@pytest.mark.asyncio
async def test_full_flow_deepen_with_explicit_question(tmp_path, monkeypatch):
    """/deepen <Q> 显式带参覆盖 transcript fallback — question 应 = 显式 arg."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_phase18_full_explicit")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
    from explain_engine.persistence.session import SessionStore
    from explain_engine.persistence.storage_v2 import StorageV2
    from explain_engine.schema.nodes import VariableNode

    storage = StorageV2()
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=MagicMock(text="ok"))

    async def fake_bootstrap(question, llm, **kwargs):
        return [
            VariableNode(
                id="p_001",
                name="single",
                description="d",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        ]

    async def fake_review(phenomena, input_provider, console=None):
        return phenomena

    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
    )
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
    )

    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    async for _ in ephemeral.handle_user_input("旧话题", llm):
        pass

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["新", "话题"])

    promoted = [e for e in events if e.type == "slash_deepen_promoted"]
    assert len(promoted) == 1
    sess = SessionStore().load(promoted[0].metadata["sid"])
    # 显式 arg 拼接成 "新 话题", 不是 transcript 末 "旧话题"
    assert sess.meta.question == "新 话题"
