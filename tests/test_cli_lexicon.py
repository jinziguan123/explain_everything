"""CLI `explain lexicon` 命令 — Phase 10 Wave 4."""

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.engines.lexicon import _now_iso, _save_lexicon
from explain_engine.persistence.storage_v2 import StorageV2


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_var(
    global_id: str,
    name: str,
    level: int = 2,
    reuse_count: int = 1,
    avg_essentialness: float = 0.5,
    avg_consistency: float = 0.5,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "global_id": global_id,
        "name": name,
        "description": f"desc of {name}",
        "abstraction_level": level,
        "epistemic": "insight",
        "fitness": {
            "reuse_count": reuse_count,
            "avg_essentialness": avg_essentialness,
            "avg_consistency": avg_consistency,
            "first_seen_at": now,
            "last_seen_at": now,
        },
        "canonical_mechanism": f"通常 cause stuff (var={name})",
        "source_sessions": ["s_001"],
    }


def _seed_lexicon(variables: list[dict[str, Any]]) -> None:
    """Write lexicon JSON to current EXPLAIN_HOME project knowledge dir."""
    path = StorageV2().knowledge_dir() / "variables.json"
    _save_lexicon(
        path,
        {
            "version": 1,
            "updated_at": _now_iso(),
            "variables": variables,
        },
    )


class TestCliLexicon:
    def test_empty_lexicon(self, runner: CliRunner) -> None:
        """无 lexicon 文件时, 显 dim 提示, exit 0."""
        result = runner.invoke(app, ["lexicon"])
        assert result.exit_code == 0
        assert "暂无变量" in result.output

    def test_lexicon_with_vars(self, runner: CliRunner) -> None:
        """有 vars 时, 渲染 table 含 global_id / name / Level."""
        _seed_lexicon([
            _make_var("v_aaa", "var_alpha", level=2, reuse_count=3,
                      avg_essentialness=0.8),
            _make_var("v_bbb", "var_beta", level=1, reuse_count=1,
                      avg_essentialness=0.5),
        ])
        result = runner.invoke(app, ["lexicon"])
        assert result.exit_code == 0
        assert "v_aaa" in result.output
        assert "var_alpha" in result.output
        assert "v_bbb" in result.output
        assert "var_beta" in result.output
        assert "L2" in result.output
        assert "L1" in result.output

    def test_dump_json(self, runner: CliRunner) -> None:
        """--dump-json 直 print 完整 JSON (含 version / variables)."""
        _seed_lexicon([_make_var("v_xyz", "json_var")])
        result = runner.invoke(app, ["lexicon", "--dump-json"])
        assert result.exit_code == 0
        # 解析 stdout JSON
        payload = json.loads(result.output)
        assert payload["version"] == 1
        assert len(payload["variables"]) == 1
        assert payload["variables"][0]["global_id"] == "v_xyz"
        assert payload["variables"][0]["name"] == "json_var"

    def test_top_k_filter(self, runner: CliRunner) -> None:
        """--top-k 限数, 按 composite = reuse * (ess + 0.1) 排序."""
        _seed_lexicon([
            _make_var("v_low", "low_score", reuse_count=1,
                      avg_essentialness=0.1),
            _make_var("v_mid", "mid_score", reuse_count=2,
                      avg_essentialness=0.3),
            _make_var("v_high", "high_score", reuse_count=5,
                      avg_essentialness=0.9),
        ])
        result = runner.invoke(app, ["lexicon", "--top-k", "2"])
        assert result.exit_code == 0
        # 2 Top vars: v_high (score=5.0) + v_mid (score=0.8)
        assert "v_high" in result.output
        assert "v_mid" in result.output
        # low_score 不该入 top 2
        assert "v_low" not in result.output
