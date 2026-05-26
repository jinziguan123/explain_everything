"""cli + chat 入口 light_llm wiring — Phase 17.2 Task 19.

验 explain new / ephemeral.promote_to_persistent 调 bootstrap_phenomena 时
传 light_llm=<make_light_llm_client()> (而不是 None).
"""

from unittest.mock import AsyncMock

from explain_engine.llm.client import Response


def _mk_extract_resp(n: int = 8) -> Response:
    return Response(
        text="",
        parsed={
            "phenomena": [
                {"name": f"p{i}", "description": f"d{i}"} for i in range(n)
            ]
        },
        model="mock",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


def _mk_classify_resp(t: str = "causal_modern") -> Response:
    return Response(
        text="",
        parsed={"type": t},
        model="mock-light",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


async def test_cli_run_new_passes_light_llm_to_bootstrap(
    monkeypatch, tmp_path
):
    """cli `_run_new` 调 bootstrap_phenomena 时 light_llm 非 None."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_proj")
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")

    # patch make_llm_client + make_light_llm_client 返 mock
    main_mock = AsyncMock()
    main_mock.chat = AsyncMock(return_value=_mk_extract_resp(8))
    light_mock = AsyncMock()
    light_mock.chat = AsyncMock(return_value=_mk_classify_resp("causal_modern"))

    monkeypatch.setattr(
        "explain_engine.cli.make_llm_client", lambda: main_mock
    )
    monkeypatch.setattr(
        "explain_engine.cli.make_light_llm_client", lambda: light_mock
    )

    # patch bootstrap_phenomena to capture kwargs
    captured_kwargs = {}

    async def fake_bootstrap(question, llm, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["_llm"] = llm
        # 返空 list 跳后续 HITL / save
        return []

    monkeypatch.setattr(
        "explain_engine.cli.bootstrap_phenomena", fake_bootstrap
    )

    # patch review_phenomena / SessionStore.save 等下游避免噪声
    monkeypatch.setattr(
        "explain_engine.cli.review_phenomena", lambda phenomena, console: []
    )

    from explain_engine.cli import _run_new

    # no_chat=True 跳过 chat REPL 的 _run_chat_repl_async
    await _run_new("为什么 X", no_chat=True)

    # 关键 assertion: light_llm 被传 + 是我们 mock 的 light_mock
    assert "light_llm" in captured_kwargs
    assert captured_kwargs["light_llm"] is light_mock


async def test_ephemeral_promote_passes_light_llm_to_bootstrap(
    monkeypatch, tmp_path
):
    """ephemeral.promote_to_persistent 调 bootstrap_phenomena 时 light_llm 非 None."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_proj")
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")

    main_mock = AsyncMock()
    main_mock.chat = AsyncMock(return_value=_mk_extract_resp(8))
    light_mock = AsyncMock()
    light_mock.chat = AsyncMock(return_value=_mk_classify_resp("causal_modern"))

    # patch make_light_llm_client (ephemeral 应该在 promote 内调它)
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.make_light_llm_client",
        lambda: light_mock,
    )

    captured_kwargs = {}

    async def fake_bootstrap(question, llm, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["_llm"] = llm
        return []

    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
    )

    # review_phenomena_async 全 accept stub
    async def fake_review(phenomena, input_provider):
        return phenomena

    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
    )

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    eph = EphemeralChatSession(storage=storage)

    await eph.promote_to_persistent("为什么 Y", main_mock)

    assert "light_llm" in captured_kwargs
    assert captured_kwargs["light_llm"] is light_mock
