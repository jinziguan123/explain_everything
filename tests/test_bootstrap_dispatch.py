"""bootstrap_phenomena classify dispatch — Phase 17.2 Task 17+18."""

from unittest.mock import AsyncMock

from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.llm.client import Response


def _classify_resp(type_str: str) -> Response:
    return Response(
        text="",
        parsed={"type": type_str},
        model="mock-light",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


def _extract_resp(count: int = 8) -> Response:
    return Response(
        text="",
        parsed={
            "phenomena": [
                {"name": f"p{i}", "description": f"d{i}"} for i in range(count)
            ]
        },
        model="mock-main",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


async def test_bootstrap_concept_explanation_loads_correct_yaml(monkeypatch):
    """classify 返 concept_explanation → 加载 variable_extraction_concept_explanation.yaml."""
    loaded_yamls = []

    real_load_prompt = None
    from explain_engine.llm.prompts import _loader as _loader_mod
    real_load_prompt = _loader_mod.load_prompt

    def fake_load_prompt(name):
        loaded_yamls.append(name)
        # 返真实 yaml (避 KeyError on missing fields), 但记录加载名
        return real_load_prompt(name)

    monkeypatch.setattr(
        "explain_engine.engines.bootstrap.load_prompt", fake_load_prompt
    )

    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_classify_resp("concept_explanation"))
    main.chat = AsyncMock(return_value=_extract_resp(8))

    await bootstrap_phenomena(
        "为什么易经里有用九用六", main, light_llm=light
    )

    assert "question_classify" in loaded_yamls
    assert "variable_extraction_concept_explanation" in loaded_yamls


async def test_bootstrap_mechanism_loads_correct_yaml(monkeypatch):
    """classify 返 mechanism → 加载 variable_extraction_mechanism.yaml."""
    loaded_yamls = []
    from explain_engine.llm.prompts import _loader as _loader_mod
    real_load_prompt = _loader_mod.load_prompt

    def fake_load_prompt(name):
        loaded_yamls.append(name)
        return real_load_prompt(name)

    monkeypatch.setattr(
        "explain_engine.engines.bootstrap.load_prompt", fake_load_prompt
    )

    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_classify_resp("mechanism"))
    main.chat = AsyncMock(return_value=_extract_resp(10))

    await bootstrap_phenomena(
        "为什么 Raft 用 majority quorum", main, light_llm=light
    )

    assert "variable_extraction_mechanism" in loaded_yamls


async def test_bootstrap_phenomenon_loads_correct_yaml(monkeypatch):
    """classify 返 phenomenon → 加载 variable_extraction_phenomenon.yaml."""
    loaded_yamls = []
    from explain_engine.llm.prompts import _loader as _loader_mod
    real_load_prompt = _loader_mod.load_prompt

    def fake_load_prompt(name):
        loaded_yamls.append(name)
        return real_load_prompt(name)

    monkeypatch.setattr(
        "explain_engine.engines.bootstrap.load_prompt", fake_load_prompt
    )

    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_classify_resp("phenomenon"))
    main.chat = AsyncMock(return_value=_extract_resp(8))

    await bootstrap_phenomena("为什么彩虹七色", main, light_llm=light)

    assert "variable_extraction_phenomenon" in loaded_yamls


async def test_bootstrap_causal_modern_classify_loads_default_yaml(monkeypatch):
    """classify 返 causal_modern → 加载 variable_extraction.yaml (default)."""
    loaded_yamls = []
    from explain_engine.llm.prompts import _loader as _loader_mod
    real_load_prompt = _loader_mod.load_prompt

    def fake_load_prompt(name):
        loaded_yamls.append(name)
        return real_load_prompt(name)

    monkeypatch.setattr(
        "explain_engine.engines.bootstrap.load_prompt", fake_load_prompt
    )

    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_classify_resp("causal_modern"))
    main.chat = AsyncMock(return_value=_extract_resp(8))

    await bootstrap_phenomena("为什么年轻人不消费", main, light_llm=light)

    assert "variable_extraction" in loaded_yamls
    # 不应该加载 variable_extraction_causal_modern
    assert "variable_extraction_causal_modern" not in loaded_yamls


async def test_bootstrap_without_light_llm_zero_regression(monkeypatch):
    """light_llm=None → 跳 classify, 走 variable_extraction.yaml (= 现行为)."""
    loaded_yamls = []
    from explain_engine.llm.prompts import _loader as _loader_mod
    real_load_prompt = _loader_mod.load_prompt

    def fake_load_prompt(name):
        loaded_yamls.append(name)
        return real_load_prompt(name)

    monkeypatch.setattr(
        "explain_engine.engines.bootstrap.load_prompt", fake_load_prompt
    )

    main = AsyncMock()
    main.chat = AsyncMock(return_value=_extract_resp(8))

    # 不传 light_llm — backward compat path
    await bootstrap_phenomena("为什么年轻人不消费", main)

    # 不该有 classify yaml
    assert "question_classify" not in loaded_yamls
    # 只加载 variable_extraction.yaml
    assert "variable_extraction" in loaded_yamls
    # main.chat 只调一次 (没 classify call)
    assert main.chat.call_count == 1


# ----- Task 18: classify 全挂 → causal_modern fallback (集成验) -----


async def test_bootstrap_classify_fail_falls_back_to_causal_modern(monkeypatch):
    """light + main classify 都挂 → bootstrap 走 variable_extraction.yaml (causal_modern), 不 raise."""
    from explain_engine.llm.errors import LLMError

    loaded_yamls = []
    from explain_engine.llm.prompts import _loader as _loader_mod
    real_load_prompt = _loader_mod.load_prompt

    def fake_load_prompt(name):
        loaded_yamls.append(name)
        return real_load_prompt(name)

    monkeypatch.setattr(
        "explain_engine.engines.bootstrap.load_prompt", fake_load_prompt
    )

    light = AsyncMock()
    light.chat = AsyncMock(side_effect=LLMError("light net down"))

    main = AsyncMock()
    # main 第 1 次 (classify fallback) raise, 第 2 次 (extraction) 成功
    main.chat = AsyncMock(
        side_effect=[
            LLMError("classify backup also fails"),
            _extract_resp(8),
        ]
    )

    # 不应 raise — classify 全挂 fallback 到 causal_modern, 用 variable_extraction.yaml
    phenomena = await bootstrap_phenomena(
        "anything", main, light_llm=light
    )

    # variable_extraction (causal_modern default) 加载
    assert "variable_extraction" in loaded_yamls
    # 现象正常 8 个
    assert len(phenomena) == 8
    # classify 调了 light + main, extraction 调了 main 1 次, 共 main=2
    assert main.chat.call_count == 2
    assert light.chat.call_count == 1
