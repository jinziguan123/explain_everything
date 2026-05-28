"""Phase 19 真终端 Bug B: PG lexicon dict 跟 JSON tuple 形式不同 → unpacking 崩.

# 真因 (第一性原理)

User 真终端跑 `/compress` 报:
    /compress 失败: ValueError: too many values 头  (后半截被 cut)

错误全文应为 `ValueError: too many values to unpack (expected 3)`.

调研:
- `engines/compression.py:73` `_render_lexicon_topk_section`:
      for global_id, canonical, reuse_count in existing_lexicon: ...
- `existing_lexicon` 来源 `engines/lexicon.py:810 get_lexicon_top_k_for_compress`
  (Wave 9 dispatcher):
    * **JSON backend**: 返 `list[tuple[str, str, int]]` (定义在 lexicon.py:460)
    * **PG backend**:   返 `list[dict[str, Any]]` (定义在 lexicon_pg.py:556)

Python `for a, b, c in d` 对 dict 时 iterate dict.keys() — keys 是 column
名 (e.g. "global_id", "name", "description", "abstraction_level", "epistemic",
"canonical_mechanism", "reuse_count", ...) 共 14 项. 试 `a, b, c = "global_id"`
失败: 字符串 "global_id" 有 9 个 char → "too many values to unpack (expected 3)".

PG backend 启 (production 用户环境) 才踩, JSON backend (开发/CI) 不踩 → 单 path
test 不覆盖. Phase 17.1 Wave 4 加 PG backend 时 `_render_lexicon_topk_section`
没同步, 一直是隐藏 bug.

# 修法

`_render_lexicon_topk_section` 接受 dict / tuple 两种 element shape:
- dict → 取 dict["global_id"] / dict["canonical_mechanism"] / dict["reuse_count"]
- tuple/list → 拆 [0]/[1]/[2] (保留 backward compat 跟 JSON tuple 路径)

# Test (TDD red → green)

本文件 3 个 test:
1. test_render_pg_dict_format — PG backend 模拟: list[dict], 应渲染成功
2. test_render_json_tuple_format — JSON backend 老路径: list[tuple], 应仍可
3. test_propose_candidates_with_pg_dict_lexicon — 端到端 propose_candidates
   接 PG dict list 不崩 (集成 prompt 拼接 path)

跟 Phase 17.2 light_llm 透传无关 (那个改的是 flush_to_lexicon signature, 不动
get_lexicon_top_k_for_compress / propose_candidates), 真因是 Phase 17.1 PG
backend 引入时的形式不一致.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# ─── 1. PG dict format 应可渲染 (老 fix 前会崩) ───


def test_render_pg_dict_format() -> None:
    """PG backend 返 list[dict], _render_lexicon_topk_section 应可渲染.

    Bug B 真终端复现: PG production 路径返 dict shape, 老 for-unpack 崩.
    """
    from explain_engine.engines.compression import _render_lexicon_topk_section

    # 模拟 PG lexicon_pg.get_lexicon_top_k_for_compress 真返 (psycopg dict_row).
    # 表 schema 14 字段, 这里列关键 + 几个无关项 (验 _render 只取需要的).
    pg_top_k: list[dict] = [
        {
            "global_id": "v_aaaa1111",
            "name": "X",
            "description": "DESC1",
            "canonical_mechanism": "经济不安全感导致防御性储蓄上升",
            "reuse_count": 5,
            "abstraction_level": 1,
            "epistemic": "insight",
            "avg_essentialness": 0.9,
            "avg_consistency": 0.85,
        },
        {
            "global_id": "v_bbbb2222",
            "name": "Y",
            "description": "DESC2",
            "canonical_mechanism": "老龄化人口结构演变",
            "reuse_count": 3,
            "abstraction_level": 1,
            "epistemic": "insight",
        },
    ]

    # Should NOT raise ValueError: too many values to unpack.
    section = _render_lexicon_topk_section(pg_top_k)

    assert "已知 lexicon abstractions" in section
    assert "v_aaaa1111" in section
    assert "经济不安全感" in section
    assert "reused 5x" in section
    assert "v_bbbb2222" in section
    assert "老龄化" in section
    assert "reused 3x" in section


def test_render_json_tuple_format() -> None:
    """JSON backend 返 list[tuple] — 老路径仍应工作 (backward compat).

    fix 不能 break 老 JSON 路径.
    """
    from explain_engine.engines.compression import _render_lexicon_topk_section

    json_top_k: list[tuple[str, str, int]] = [
        ("v_aaaa1111", "经济不安全感导致防御性储蓄上升", 5),
        ("v_bbbb2222", "老龄化人口结构演变", 3),
    ]

    section = _render_lexicon_topk_section(json_top_k)
    assert "v_aaaa1111" in section
    assert "经济不安全感" in section
    assert "reused 5x" in section


def test_render_pg_dict_long_canonical_truncated() -> None:
    """PG dict path 也应受 80-char canonical 截断 (跟 tuple 路径一致)."""
    from explain_engine.engines.compression import _render_lexicon_topk_section

    long_canonical = "y" * 100
    pg_top_k = [{
        "global_id": "v_aaaa1111",
        "canonical_mechanism": long_canonical,
        "reuse_count": 1,
    }]
    section = _render_lexicon_topk_section(pg_top_k)
    assert "y" * 80 + "..." in section


# ─── 2. 端到端 propose_candidates 接 PG dict (集成 path) ───


def _setup_state():
    """跟 tests/test_engines_compression.py 同款 CognitiveState fixture."""
    from explain_engine.schema.nodes import VariableNode
    from explain_engine.schema.state import CognitiveState

    state = CognitiveState.bootstrap("why compress", budget=20)
    for i in range(1, 5):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"现象{i}",
                description=f"现象{i}描述",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    return state


def _candidate(name: str, coverage_ids: list[str]) -> dict:
    return {
        "name": name,
        "description": f"{name}的定义",
        "coverage": [
            {"concrete_id": cid, "mechanism": f"{name} → {cid}"}
            for cid in coverage_ids
        ],
    }


def _mock_llm_response(candidates: list[dict]) -> object:
    """跟 tests/test_engines_compression.py 一致, 用 Response(text/parsed/model/usage)."""
    from explain_engine.llm.client import Response

    return Response(
        text="",
        parsed={"candidates": candidates},
        model="test",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
async def test_propose_candidates_with_pg_dict_lexicon() -> None:
    """propose_candidates 接 PG dict list 不崩 (端到端集成 path).

    模拟真终端 `/compress` 调用: `get_lexicon_top_k_for_compress` 返 PG dict
    list, 传给 propose_candidates → 内部调 _render_lexicon_topk_section.
    """
    from explain_engine.engines.compression import propose_candidates

    state = _setup_state()
    llm = AsyncMock()
    llm.chat.return_value = _mock_llm_response([
        _candidate("Concept A", ["p_001", "p_002", "p_003"]),
        _candidate("Concept B", ["p_002", "p_003"]),
        _candidate("Concept C", ["p_001", "p_004"]),
    ])

    pg_lexicon = [
        {
            "global_id": "v_existing1",
            "canonical_mechanism": "已有 lexicon canonical mechanism",
            "reuse_count": 2,
            "name": "Existing",
            "description": "Existing desc",
        }
    ]

    # 关键: 老 fix 前会抛 ValueError 在 _render_lexicon_topk_section.
    await propose_candidates(
        state=state,
        llm=llm,
        existing_lexicon=pg_lexicon,  # PG dict shape (老会崩)
    )

    # 验 LLM message 内含已知 lexicon section + dict 来的字段
    call_args = llm.chat.await_args
    messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
    user_msg = messages[1].content
    assert "已知 lexicon abstractions" in user_msg
    assert "v_existing1" in user_msg
    assert "reused 2x" in user_msg
    assert "已有 lexicon canonical mechanism" in user_msg
