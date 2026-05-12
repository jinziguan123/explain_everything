from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from explain_agent.storage.snapshot import SnapshotStore


def test_save_writes_file_and_returns_id(tmp_path):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    snap_id = store.save("正文内容测试", content_type="news")

    assert snap_id.startswith("snap_")

    today = datetime.now()
    expected = tmp_path / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}" / f"{snap_id}.txt"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "正文内容测试"

    mock_conn.exec_driver_sql.assert_called_once()
    call = mock_conn.exec_driver_sql.call_args
    assert "explain_snapshot_blob" in call.args[0]
    params = call.args[1]
    assert params[0] == snap_id
    assert params[1] == "news"
    assert str(expected) in params[2]


def test_load_reads_existing_snapshot(tmp_path):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    snap_id = store.save("hello", content_type="news")

    out = store.load(snap_id)
    assert out == "hello"


def test_load_returns_none_when_missing(tmp_path):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    out = store.load("snap_nonexistent")
    assert out is None


def test_save_handles_empty_content(tmp_path):
    """空内容不写文件，返回 None。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    snap_id = store.save("", content_type="news")
    assert snap_id is None
    mock_conn.exec_driver_sql.assert_not_called()
