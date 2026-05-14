"""expansion.yaml prompt loader 测试。"""

from explain_engine.llm.prompts._loader import load_prompt


def test_expansion_yaml_loads() -> None:
    p = load_prompt("expansion")
    assert "system" in p
    assert "user_template" in p


def test_expansion_yaml_has_required_placeholders() -> None:
    p = load_prompt("expansion")
    tpl = p["user_template"]
    for ph in ("{question}", "{target_node}", "{target_outgoing_edges}", "{existing_drivers}"):
        assert ph in tpl, f"missing placeholder {ph!r} in expansion user_template"


def test_expansion_system_mentions_driver() -> None:
    p = load_prompt("expansion")
    sys = p["system"]
    # 哲学锚: driver 必须是可检验机制变量，不是 cosmic 哲学名词
    assert "driver" in sys.lower()
