"""variable_extraction_<type>.yaml 4 个 prompt 结构验证 — Phase 17.2."""

from explain_engine.llm.prompts._loader import load_prompt


def test_concept_explanation_yaml_loads():
    """concept_explanation: 本域 ≥ 50% + 跨域 ≤ 50%."""
    p = load_prompt("variable_extraction_concept_explanation")
    assert "system" in p
    assert "user_template" in p
    # 关键 invariant: 50/50 split
    assert "50%" in p["system"]
    assert "本域" in p["system"]
    # user_template 必须 fmt 占位
    assert "{question}" in p["user_template"]
    assert "{min_count}" in p["user_template"]
    assert "{max_count}" in p["user_template"]
