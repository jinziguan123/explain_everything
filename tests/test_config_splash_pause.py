"""Phase 20.1 #5: LLM_SPLASH_PAUSE_S env knob validation.

设计 doc 3c8edb8 §#5: tui_app _splash_lifecycle 5.0s hardcode → env knob.
helper _read_splash_pause_env() in config.py, 跟 _read_read_timeout_env 同
validation pattern.

行为:
- 未设 / 空 → default 5.0 (保 Wave 7 决策让 user 真看见 splash hold)
- 设了非负 float → 返该值
- 0 允许 (user 主动跳 hold)
- negative → ValueError "must be >= 0"
- non-float → ValueError 含 env name + offending value
"""
from __future__ import annotations

import pytest

from explain_engine.config import _read_splash_pause_env


def test_read_splash_pause_default_5(monkeypatch):
    """env 未设 → 默 5.0."""
    monkeypatch.delenv("LLM_SPLASH_PAUSE_S", raising=False)
    assert _read_splash_pause_env() == 5.0


def test_read_splash_pause_env_override(monkeypatch):
    """LLM_SPLASH_PAUSE_S=2.5 → 2.5."""
    monkeypatch.setenv("LLM_SPLASH_PAUSE_S", "2.5")
    assert _read_splash_pause_env() == 2.5


def test_read_splash_pause_zero_allowed(monkeypatch):
    """LLM_SPLASH_PAUSE_S=0 → 0.0 (user 主动跳 splash hold)."""
    monkeypatch.setenv("LLM_SPLASH_PAUSE_S", "0")
    assert _read_splash_pause_env() == 0.0


def test_read_splash_pause_invalid_raises(monkeypatch):
    """LLM_SPLASH_PAUSE_S=abc → ValueError with env name in msg."""
    monkeypatch.setenv("LLM_SPLASH_PAUSE_S", "abc")
    with pytest.raises(ValueError, match="LLM_SPLASH_PAUSE_S"):
        _read_splash_pause_env()


def test_read_splash_pause_negative_raises(monkeypatch):
    """LLM_SPLASH_PAUSE_S=-1 → ValueError "must be >= 0"."""
    monkeypatch.setenv("LLM_SPLASH_PAUSE_S", "-1")
    with pytest.raises(ValueError, match="must be >= 0"):
        _read_splash_pause_env()
