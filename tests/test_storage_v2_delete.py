"""StorageV2.delete_session — Phase 17.2 Task 20.

测试 rmtree session_dir, 不存在 → FileNotFoundError.
"""
import pytest

from explain_engine.persistence.storage_v2 import StorageV2


def test_delete_session_removes_dir() -> None:
    """delete_session 必须把 session_dir 整个删掉."""
    s = StorageV2()
    s.save_metadata(
        "s_aaaaaaaa",
        {
            "session_id": "s_aaaaaaaa",
            "question": "q",
            "stage": "bootstrap_pending",
            "created_at": 0.0,
            "updated_at": 0.0,
        },
    )
    assert s.session_dir("s_aaaaaaaa").exists()

    s.delete_session("s_aaaaaaaa")
    assert not s.session_dir("s_aaaaaaaa").exists()


def test_delete_session_nonexistent_raises() -> None:
    """不存在的 sid → FileNotFoundError (caller 上层抓 → 友好 zh msg)."""
    s = StorageV2()
    with pytest.raises(FileNotFoundError):
        s.delete_session("s_notexist")


def test_delete_session_removes_sidecars() -> None:
    """delete_session 删整个 dir, 包括 metadata + graph + transcript + chat_state +
    memory + repl_history 5 sidecar 全清干净."""
    s = StorageV2()
    sid = "s_bbbbbbbb"
    s.save_metadata(
        sid,
        {
            "session_id": sid,
            "question": "q",
            "stage": "bootstrap_pending",
            "created_at": 0.0,
            "updated_at": 0.0,
        },
    )
    s.save_graph(sid, {"graph": {"nodes": {}, "edges": []}, "tick": 0})
    s.append_transcript(sid, {"role": "user", "content": "hi"})
    s.save_memory(sid, "memo")
    s.save_chat_state(sid, {"turn_count": 1})
    s.append_repl_history(sid, {"ts": "x", "type": "slash", "cmd": "save"})

    d = s.session_dir(sid)
    for name in (
        "metadata.json",
        "graph.json",
        "transcript.jsonl",
        "memory.md",
        "chat_state.json",
        "repl_history.jsonl",
    ):
        assert (d / name).exists(), f"setup: {name} 应该已写入"

    s.delete_session(sid)
    assert not d.exists()
