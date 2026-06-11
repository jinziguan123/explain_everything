"""H1 盲评 harness 测试 (docs/设计预期-修正版.md §四 H1)。

orchestration 层测试: pipeline/baseline 走依赖注入的 fake, 验证
输出文件结构、盲化映射确定性、manifest、单题失败不中断。
引擎本身的正确性由 test_engines_* 覆盖, 此处不重复。
不触网 / 不触 PG / 不读 .env。
"""

import asyncio
import json

from explain_engine.bench.harness import (
    _blind_assignment,
    counterintuitiveness,
    run_bench,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _tiny_state(question: str) -> CognitiveState:
    state = CognitiveState.bootstrap(question, budget=5)
    state.graph.add_node(VariableNode(
        id="p_001", name="现象一", description="d", abstraction_level=0,
        confidence=0.8, epistemic="observation",
    ))
    state.graph.add_node(VariableNode(
        id="c_001", name="机制一", description="d", abstraction_level=1,
        confidence=0.7, epistemic="insight",
    ))
    state.graph.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.9,
        mechanism_description="m",
    ))
    return state


async def _fake_pipeline(q: str):
    return _tiny_state(q), {"stop_reason": "fake", "timings": {}}


async def _fake_baseline(q: str) -> str:
    return f"B组报告: {q}"


# ─── 盲化 ──────────────────────────────────────────────────


def test_blind_assignment_deterministic():
    a = _blind_assignment(42, 1, "为什么 X")
    for _ in range(3):
        assert _blind_assignment(42, 1, "为什么 X") == a


def test_blind_assignment_varies_across_questions():
    # 同 seed 下 20 题不应全部同侧 (否则等于没盲化)
    sides = {_blind_assignment(42, i, f"q{i}") for i in range(20)}
    assert sides == {True, False}


# ─── 反直觉度 ──────────────────────────────────────────────


def test_counterintuitiveness_all_expected():
    assert counterintuitiveness(["房价上涨"], ["房价上涨", "工资低"]) == 0.0


def test_counterintuitiveness_all_novel():
    assert counterintuitiveness(["身份焦虑", "原子化"], ["房价上涨"]) == 1.0


def test_counterintuitiveness_containment_match():
    # 归一化互相包含算命中: "长期收入预期" ⊂ "长期收入预期恶化"
    assert counterintuitiveness(["长期收入预期恶化"], ["长期收入预期"]) == 0.0


def test_counterintuitiveness_empty_core():
    assert counterintuitiveness([], ["x"]) == 0.0


# ─── run_bench 端到端 (注入 fake) ──────────────────────────


def test_run_bench_layout_and_blinding(tmp_path):
    questions = ["为什么年轻人不消费", "为什么大模型公司亏损"]
    manifest = asyncio.run(run_bench(
        questions, tmp_path,
        pipeline_fn=_fake_pipeline, baseline_fn=_fake_baseline, seed=7,
    ))

    assert len(manifest["questions"]) == 2
    assert all(q["status"] == "ok" for q in manifest["questions"])

    for i, q in enumerate(questions, start=1):
        qdir = tmp_path / f"q{i:02d}"
        x = (qdir / "X.md").read_text(encoding="utf-8")
        y = (qdir / "Y.md").read_text(encoding="utf-8")
        key = json.loads((qdir / ".key.json").read_text(encoding="utf-8"))
        # 映射与内容一致: B 组报告有固定前缀, A 组 (无 llm → dossier) 含档案标题
        b_side = "X" if key["X"] == "B" else "Y"
        b_text = x if b_side == "X" else y
        a_text = y if b_side == "X" else x
        assert b_text.startswith("B组报告")
        assert "图谱档案" in a_text
        assert q in a_text
        # 揭盲后图谱可检视
        graph = json.loads(
            (qdir / ".artifacts" / "graph.json").read_text(encoding="utf-8")
        )
        assert graph["root_question"] == q

    # 根目录材料
    sheet = (tmp_path / "评分表.md").read_text(encoding="utf-8")
    assert "Q1" in sheet and "Q2" in sheet and "q02" in sheet
    answers = json.loads((tmp_path / ".answers.json").read_text(encoding="utf-8"))
    assert set(answers) == {"q01", "q02"}
    saved_manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    # manifest 不含揭盲映射 (盲评期间可安全查看)
    assert "X" not in json.dumps(saved_manifest)


def test_run_bench_seed_changes_assignment_deterministically(tmp_path):
    """同题同 seed 跑两次映射一致; 不要求不同 seed 必然翻面 (50% 概率)。"""
    questions = ["为什么 A"]

    async def run(seed: int, sub: str) -> dict:
        await run_bench(
            questions, tmp_path / sub,
            pipeline_fn=_fake_pipeline, baseline_fn=_fake_baseline, seed=seed,
        )
        return json.loads(
            (tmp_path / sub / "q01" / ".key.json").read_text(encoding="utf-8")
        )

    k1 = asyncio.run(run(1, "r1"))
    k2 = asyncio.run(run(1, "r2"))
    assert k1 == k2


def test_run_bench_single_failure_does_not_abort(tmp_path):
    async def flaky_pipeline(q: str):
        if "坏" in q:
            raise RuntimeError("pipeline 炸了")
        return await _fake_pipeline(q)

    manifest = asyncio.run(run_bench(
        ["好问题", "坏问题", "好问题2"], tmp_path,
        pipeline_fn=flaky_pipeline, baseline_fn=_fake_baseline,
    ))
    statuses = [q["status"] for q in manifest["questions"]]
    assert statuses == ["ok", "error", "ok"]
    assert "RuntimeError" in manifest["questions"][1]["error"]
    # 失败题不产出盲评材料, 成功题正常
    assert not (tmp_path / "q02" / "X.md").exists()
    assert (tmp_path / "q03" / "X.md").exists()


def test_run_bench_requires_llm_or_injection(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        asyncio.run(run_bench(["q"], tmp_path))
