"""TraceEntry — Phase 5 reasoning_trace 单条记录。"""

import pytest

from explain_engine.schema.state import TraceEntry


class TestTraceEntry:
    def test_construct_minimal(self) -> None:
        entry = TraceEntry(
            tick=0,
            action="expand",
            target_node_id="c_001",
            gain_delta=0.42,
            llm_calls=1,
            timestamp="2026-05-13T10:00:00",
        )
        assert entry.tick == 0
        assert entry.action == "expand"
        assert entry.target_node_id == "c_001"
        assert entry.gain_delta == 0.42

    def test_target_node_id_optional(self) -> None:
        entry = TraceEntry(
            tick=4,
            action="evaluate",
            target_node_id=None,
            gain_delta=0.0,
            llm_calls=0,
            timestamp="2026-05-13T10:00:01",
        )
        assert entry.target_node_id is None

    def test_action_literal_validated(self) -> None:
        with pytest.raises(ValueError, match="invalid action"):
            TraceEntry(
                tick=0,
                action="bogus",  # type: ignore[arg-type]
                target_node_id=None,
                gain_delta=0.0,
                llm_calls=0,
                timestamp="2026-05-13T10:00:00",
            )

    def test_negative_tick_rejected(self) -> None:
        with pytest.raises(ValueError, match="tick must be >= 0"):
            TraceEntry(
                tick=-1,
                action="expand",
                target_node_id="c_001",
                gain_delta=0.0,
                llm_calls=0,
                timestamp="2026-05-13T10:00:00",
            )

    def test_negative_llm_calls_rejected(self) -> None:
        with pytest.raises(ValueError, match="llm_calls must be >= 0"):
            TraceEntry(
                tick=0,
                action="expand",
                target_node_id="c_001",
                gain_delta=0.0,
                llm_calls=-1,
                timestamp="2026-05-13T10:00:00",
            )

    def test_to_dict_from_dict_roundtrip(self) -> None:
        entry = TraceEntry(
            tick=3,
            action="expand",
            target_node_id="c_002",
            gain_delta=0.6,
            llm_calls=1,
            timestamp="2026-05-13T11:00:00",
        )
        d = entry.to_dict()
        recovered = TraceEntry.from_dict(d)
        assert recovered == entry
