"""question_classify.yaml load test — Phase 17.2 Task 11."""

from explain_engine.llm.prompts._loader import load_prompt


def test_question_classify_yaml_loads():
    p = load_prompt("question_classify")
    assert "system" in p
    assert "user_template" in p
    assert "causal_modern" in p["system"]
    assert "concept_explanation" in p["system"]
    assert "mechanism" in p["system"]
    assert "phenomenon" in p["system"]
