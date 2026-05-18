"""Phase 11 Wave 1: EphemeralChatSession — in-memory chat before persistent /new.

REPL 启动时建. 用户输自然语言时 promote_to_persistent 把 question 跑
bootstrap+HITL+save, 切到真 ChatSession.

设计: docs/plans/2026-05-18-phase11-repl-unification-design.md §5
Plan: docs/plans/2026-05-18-phase11-repl-unification-plan.md Wave 1 Task 1.A
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from explain_engine.chat.session import ChatSession, ChatStateDict
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena_async
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState

if TYPE_CHECKING:
    from explain_engine.llm.client import LLMClient
    from explain_engine.persistence.storage_v2 import StorageV2


@dataclass
class EphemeralChatSession:
    """In-memory chat session pre-persistence.

    REPL 启动时建. 字段:
    - storage: StorageV2 实例 (用于 promote 时读 knowledge/variables.json lexicon)
    - llm: optional LLM client (REPL 启动时注入, promote 时也可显式传)
    - state: 空 CognitiveState (graph empty, budget=0)
    - chat_state: 默认 ChatStateDict (budget 10/50) — promote 后拷到 real ChatSession
    - transcript: 空 list (ephemeral 不持久化)
    - input_provider: REPL 注入 async input callable (slash handler / HITL 用)
    - memory_md: 空 (ephemeral 不写 memory)

    Properties:
    - sid: 永 None
    - is_ephemeral: 永 True

    Method:
    - promote_to_persistent(question, llm): 跑 bootstrap + HITL + save → real ChatSession.
      失败 (LLMError / SchemaValidationError) → 抛, caller 留 ephemeral.
    """

    storage: StorageV2
    llm: LLMClient | None = None
    state: CognitiveState = field(
        default_factory=lambda: CognitiveState.bootstrap("", budget=0)
    )
    chat_state: ChatStateDict = field(default_factory=ChatStateDict)
    transcript: list[dict] = field(default_factory=list)
    input_provider: Callable[[str], Awaitable[str]] | None = None
    memory_md: str = ""

    @property
    def sid(self) -> None:
        return None

    @property
    def is_ephemeral(self) -> bool:
        return True

    async def promote_to_persistent(
        self,
        question: str,
        llm: LLMClient,
    ) -> ChatSession:
        """Trigger bootstrap + HITL + save, 返 real ChatSession.

        失败 (bootstrap LLM error / SchemaValidationError) → 抛, caller 留 ephemeral.

        步骤:
        1. Load lexicon prior (Phase 10) — 没 lexicon 时 lexicon=[]
        2. bootstrap_phenomena(question, llm, lexicon=...) — LLM call (可抛 LLMError)
        3. review_phenomena_async(phenomena, input_provider) — Wave 1 stub 全 accept;
           Wave 2 接 k/e/d interactive 流
        4. Build CognitiveState + Session + save (SessionStore)
        5. Build real ChatSession(sid, llm) + 拷 ephemeral.chat_state (含 /budget 改的)
        6. 拷 input_provider 让 sub-prompt 仍走 prompt_toolkit
        """
        # Phase 10 lexicon prior. 本地 import 避 module-load 时拉 lexicon (engines).
        from explain_engine.engines.lexicon import _load_lexicon

        lexicon_path = self.storage.knowledge_dir() / "variables.json"
        lexicon_data = _load_lexicon(lexicon_path)
        lexicon = lexicon_data["variables"]

        # bootstrap (raise → caller 留 ephemeral; ephemeral.state 不动)
        phenomena = await bootstrap_phenomena(
            question,
            llm,
            lexicon=lexicon if lexicon else None,
            lexicon_top_k=20,
        )

        # HITL — Wave 1 stub: 全 accept. Wave 2 接 input_provider + k/e/d.
        final_phenomena = await review_phenomena_async(
            phenomena,
            self.input_provider,
        )

        # Build state + meta + save
        from explain_engine.config import Settings

        settings = Settings()
        new_state = CognitiveState.bootstrap(
            question, budget=settings.default_budget
        )
        for p in final_phenomena:
            new_state.graph.add_node(p)

        meta = SessionMeta.new(question=question)
        session = Session(meta=meta, state=new_state)
        SessionStore().save(session)

        # Build real ChatSession + 拷 ephemeral.chat_state (含 /budget 改的 limit)
        real_chat = ChatSession(meta.session_id, llm=llm)
        real_chat.chat_state = self.chat_state
        real_chat.input_provider = self.input_provider
        return real_chat
