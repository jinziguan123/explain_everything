"""Phase 3 smoke: 跑真 LLM Bootstrap，跳过 HITL（自动 keep all），落 session。

用法：
    cd /Users/jinziguan/Desktop/explain_everything
    uv run python scripts/smoke_phase3.py "为什么年轻人不消费"

需要 .env 里有以下任一组：
    LLM_PROVIDER=deepseek + DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL (default https://api.deepseek.com)
    LLM_PROVIDER=claude + ANTHROPIC_API_KEY
    LLM_PROVIDER=openai + OPENAI_API_KEY
"""

import asyncio
import sys

from explain_engine.config import Settings, make_client
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState


async def main(question: str) -> None:
    settings = Settings()
    print(f"[INFO] Provider: {settings.llm_provider}, Model: {settings.llm_model}")

    llm = make_client(settings)

    print(f"[INFO] 调 LLM 生现象: {question!r}")
    phenomena = await bootstrap_phenomena(question, llm)

    print(f"\n[INFO] 生成 {len(phenomena)} 个现象:\n")
    for p in phenomena:
        print(f"  {p.id}: {p.name}")
        print(f"        {p.description}")
        print()

    # 跳过 HITL，全部 keep
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in phenomena:
        state.graph.add_node(p)

    meta = SessionMeta.new(question=question)
    store = SessionStore(directory=settings.sessions_dir)
    store.save(Session(meta=meta, state=state))

    print(f"[INFO] Session {meta.session_id} 已保存 (跳过 HITL，全 keep)")
    print(f"[INFO] 查看: uv run explain show {meta.session_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_phase3.py <question>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
