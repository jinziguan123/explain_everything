from unittest.mock import MagicMock
from datetime import datetime

import pytest

from explain_agent.cli.repl.commands import handle_annotate
from explain_agent.cli.repl.state import ReplState


def _make_session(threads):
    return {
        "session_id": "s_x",
        "target": "半导体",
        "connection_threads": threads,
        "connection_section": "",
    }


def test_annotate_lists_threads_and_writes_db():
    """两个 thread 全部打标, INSERT 调用 2 次。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    # 第一次查已有标注 → 空
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []
    console = MagicMock()
    state = ReplState()
    state.current_session_id = "s_x"
    state.current_session = _make_session([
        {"title": "T1", "hypothesis": "h", "content": "c1",
         "evidence_ids": ["e1"], "source": "web", "confidence": 4},
        {"title": "T2", "hypothesis": "h", "content": "c2",
         "evidence_ids": ["e2"], "source": "local", "confidence": 3},
    ])

    inputs = iter(["g", "联想到了 TGV", "y", ""])
    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: next(inputs))

    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_annotation" in c.args[0]]
    assert len(insert_calls) == 2
    params0 = insert_calls[0].args[1]
    assert params0[1] == "s_x"
    assert params0[2] == 0           # thread_index
    assert params0[3] == "T1"        # thread_title
    assert params0[4] == "green"
    assert params0[5] == "联想到了 TGV"


def test_annotate_skips_already_annotated():
    """已标过的 thread 跳过, 只标新的。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    # 假装 thread_index=0 已标过
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [(0,)]
    console = MagicMock()
    state = ReplState()
    state.current_session_id = "s_x"
    state.current_session = _make_session([
        {"title": "T1", "hypothesis": "", "content": "", "evidence_ids": [],
         "source": "local", "confidence": 4},
        {"title": "T2", "hypothesis": "", "content": "", "evidence_ids": [],
         "source": "local", "confidence": 4},
    ])

    inputs = iter(["r", "跑题了"])
    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: next(inputs))

    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_annotation" in c.args[0]]
    assert len(insert_calls) == 1
    params = insert_calls[0].args[1]
    assert params[2] == 1            # thread_index 1 (跳过 0)
    assert params[4] == "red"


def test_annotate_no_active_session_prints_warning():
    mock_engine = MagicMock()
    console = MagicMock()
    state = ReplState()
    state.current_session = None

    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: "")
    console.print.assert_called_once()
    msg = console.print.call_args.args[0]
    assert "session" in msg.lower() or "active" in msg.lower()


def test_annotate_no_threads_prints_warning():
    mock_engine = MagicMock()
    console = MagicMock()
    state = ReplState()
    state.current_session_id = "s_x"
    state.current_session = _make_session([])

    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: "")
    console.print.assert_called_once()
    msg = console.print.call_args.args[0]
    assert "connection_threads" in msg or "无" in msg


def test_stats_groups_by_label_with_phase3_recommendation():
    from explain_agent.cli.repl.commands import handle_stats

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    # 3 个 label 分布 + 5 个最近 red
    mock_conn.exec_driver_sql.side_effect = [
        MagicMock(fetchall=lambda: [("green", 9), ("yellow", 11), ("red", 3)]),
        MagicMock(fetchone=lambda: (12,)),  # session_count
        MagicMock(fetchall=lambda: [
            ("s_a", "区块链与白酒"),
            ("s_b", "ESG 与煤炭"),
        ]),
    ]
    console = MagicMock()

    handle_stats(engine=mock_engine, console=console)
    # 验证至少打印了主表 + 建议
    calls = "\n".join(str(c) for c in console.print.call_args_list)
    assert "23" in calls   # 总数
    assert "39" in calls or "39.1" in calls   # green pct
    # 13% red → 应推荐"不必启动 3-B"
    assert "3-B" in calls or "20%" in calls or "Phase 3" in calls


def test_stats_empty_db():
    from explain_agent.cli.repl.commands import handle_stats

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.side_effect = [
        MagicMock(fetchall=lambda: []),
    ]
    console = MagicMock()
    handle_stats(engine=mock_engine, console=console)
    calls = "\n".join(str(c) for c in console.print.call_args_list)
    assert "无" in calls or "0" in calls or "empty" in calls.lower()
