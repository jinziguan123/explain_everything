"""Phase 19 Task 7: 验 ChatEvent docstring 含 3 个新 type 段.

防止后续 maintenance 误删 thinking_text / status_start / status_end 的 contract 文档
(textual TUI _render_event 实装 + 后续 Phase 维护参考)。
"""

from explain_engine.chat.session import ChatEvent


def test_chat_event_docstring_describes_thinking_text():
    """thinking_text contract 段必须列 Producer (ephemeral / ChatSession query_loop)
    + Consumer (tui_app Collapsible)."""
    doc = ChatEvent.__doc__ or ""
    assert "thinking_text" in doc, "ChatEvent docstring 须有 thinking_text 段"
    # 关键字: reasoning, Collapsible
    assert "reasoning" in doc, "thinking_text 段须解释来自 Response.reasoning"
    assert "Collapsible" in doc, "thinking_text 段须指明 Consumer 走 Collapsible"


def test_chat_event_docstring_describes_status_start():
    """status_start contract 段必须列 Producer (调 LLM 前) + Consumer (LoadingIndicator)."""
    doc = ChatEvent.__doc__ or ""
    assert "status_start" in doc, "ChatEvent docstring 须有 status_start 段"
    # 关键字: LoadingIndicator (textual spinner widget)
    assert "LoadingIndicator" in doc, "status_start 段须指明 Consumer mount LoadingIndicator"


def test_chat_event_docstring_describes_status_end():
    """status_end contract 段必须列对应清 spinner 行为."""
    doc = ChatEvent.__doc__ or ""
    assert "status_end" in doc, "ChatEvent docstring 须有 status_end 段"
    # status_end 跟 status_start 是配对 contract — 描述 unmount / 清行为
    # 用关键字 "unmount" 或 "清" 任意一个验在
    assert any(
        kw in doc for kw in ("unmount", "清")
    ), "status_end 段须解释 unmount / 清 LoadingIndicator 行为"
