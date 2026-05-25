"""Wave A.1: storage_v2 project-based persistence tests."""

import json

import pytest

from explain_engine.persistence.storage_v2 import (
    StorageV2,
    compute_project_id,
)


class TestComputeProjectId:
    def test_cwd_hash_8_hex(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("EXPLAIN_PROJECT_ID", raising=False)
        monkeypatch.chdir(tmp_path)
        pid = compute_project_id()
        assert len(pid) == 8
        assert all(c in "0123456789abcdef" for c in pid)

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "myproj")
        assert compute_project_id() == "myproj"

    def test_stable_for_same_cwd(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("EXPLAIN_PROJECT_ID", raising=False)
        monkeypatch.chdir(tmp_path)
        assert compute_project_id() == compute_project_id()


class TestStorageV2:
    def test_session_dir_path(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        path = s.session_dir("s_001abc")
        assert path.parent.parent.parent == tmp_path / "projects"
        assert path.name == "s_001abc"

    def test_save_metadata_creates_dir(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {"question": "why X", "stage": "done"})
        path = s.session_dir("s_001") / "metadata.json"
        assert path.exists()
        assert json.loads(path.read_text())["question"] == "why X"

    def test_load_metadata_round_trip(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {"k": "v"})
        assert s.load_metadata("s_001") == {"k": "v"}

    def test_load_missing_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        with pytest.raises(FileNotFoundError):
            s.load_metadata("s_999")

    def test_save_graph_round_trip(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_graph("s_001", {"nodes": [], "edges": []})
        assert s.load_graph("s_001") == {"nodes": [], "edges": []}

    def test_list_sessions(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {})
        s.save_metadata("s_002", {})
        assert sorted(s.list_sessions()) == ["s_001", "s_002"]

    def test_atomic_write(self, tmp_path, monkeypatch) -> None:
        """write_atomic uses tmp + rename, no partial file on crash."""
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {"key": "value"})
        path = s.session_dir("s_001") / "metadata.json"
        # No .tmp file lingering
        tmp_files = list(path.parent.glob("*.tmp"))
        assert tmp_files == []

    def test_append_transcript_jsonl(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.append_transcript("s_001", {"role": "user", "content": "hi"})
        s.append_transcript("s_001", {"role": "assistant", "content": "hello"})
        lines = s.load_transcript("s_001")
        assert len(lines) == 2
        assert lines[0]["role"] == "user"
        assert lines[1]["content"] == "hello"

    def test_save_load_chat_state(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_chat_state("s_001", {"budget_remaining": 42})
        assert s.load_chat_state("s_001") == {"budget_remaining": 42}

    def test_save_load_memory_md(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_memory("s_001", "# Session memory\n\nsummary here")
        assert s.load_memory("s_001") == "# Session memory\n\nsummary here"

    def test_load_chat_state_missing_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        assert s.load_chat_state("s_001") is None


class TestReplHistoryAppend:
    def test_append_repl_history_creates_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2(project_id="testproj")
        entry = {
            "ts": "2026-05-25T14:00:00+08:00",
            "type": "slash",
            "cmd": "compress",
            "args": [],
            "summary": "+4 L1",
        }
        storage.append_repl_history("s_abc", entry)
        path = storage.session_dir("s_abc") / "repl_history.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == entry
