"""叙事报告模块测试 (Phase V, docs/设计预期-修正版.md §九)。

dossier 是纯函数 (0 LLM), narrative 走 FakeLLM。不触网 / 不触 PG / 不读 .env。
"""

from explain_engine.llm.client import Response
from explain_engine.report.dossier import build_dossier, core_variables, graph_stats
from explain_engine.report.narrative import REPORT_INSTRUCTIONS, generate_report
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    """记录 messages 并返回固定文本。"""

    def __init__(self, text: str = "# 解释报告: 测试") -> None:
        self._text = text
        self.call_count = 0
        self.last_messages: list = []

    async def chat(self, messages, schema=None, model=None, on_delta=None):
        self.call_count += 1
        self.last_messages = messages
        return Response(
            text=self._text, parsed=None, model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _node(nid, name, level, epistemic="observation", conf=0.8, **kw):
    return VariableNode(
        id=nid, name=name, description=f"{name}的描述",
        abstraction_level=level, confidence=conf, epistemic=epistemic, **kw,
    )


def _edge(eid, src, tgt, rtype, conf=0.7):
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rtype, confidence=conf,
        mechanism_description=f"{src}→{tgt}机制",
    )


def _converged_state() -> CognitiveState:
    """L2(d_001) → L1(c_001) → L0(p_001, p_002); p_003 为未覆盖残差。"""
    state = CognitiveState.bootstrap("为什么年轻人不消费", budget=10)
    g = state.graph
    g.add_node(_node("p_001", "储蓄率上升", 0))
    g.add_node(_node("p_002", "消费贷收缩", 0))
    g.add_node(_node("p_003", "奢侈品逆势增长", 0))
    g.add_node(_node("c_001", "预防性储蓄动机", 1, epistemic="insight"))
    g.add_node(_node("d_001", "长期收入预期恶化", 2, epistemic="insight"))
    g.add_edge(_edge("e_001", "c_001", "p_001", "manifests_as", 0.9))
    g.add_edge(_edge("e_002", "c_001", "p_002", "manifests_as", 0.8))
    g.add_edge(_edge("e_003", "d_001", "c_001", "causes", 0.85))
    return state


# ─── dossier ───────────────────────────────────────────────


def test_build_dossier_contains_all_layers_and_ids():
    dossier = build_dossier(_converged_state())
    # 三层节点 + id 引用都在
    for token in ("p_001", "c_001", "d_001", "储蓄率上升",
                  "预防性储蓄动机", "长期收入预期恶化"):
        assert token in dossier
    # 章节结构
    for section in ("现象层", "模式层", "驱动层", "关键因果链", "核心变量"):
        assert section in dossier


def test_build_dossier_reports_missing_l0_as_residual():
    dossier = build_dossier(_converged_state())
    # p_003 无入边, rollout 触达不到 → 必须作为解释残差出现
    assert "解释残差" in dossier
    assert "奢侈品逆势增长" in dossier.split("解释残差")[1].split("\n")[0]


def test_build_dossier_renders_causal_chain():
    dossier = build_dossier(_converged_state())
    chain_section = dossier.split("关键因果链")[1].split("##")[0]
    assert "长期收入预期恶化" in chain_section
    assert "储蓄率上升" in chain_section


def test_build_dossier_tier_wording_ungrounded():
    """Phase G: 未接地的图全部内容为假设级, 且档案声明未执行接地。"""
    dossier = build_dossier(_converged_state())
    assert "认知等级: 假设" in dossier
    assert "证据接地: 未执行" in dossier
    # 压缩值进档案头 (§五.1)
    assert "压缩值 CV" in dossier


def test_build_dossier_with_prior_causes():
    dossier = build_dossier(_converged_state(), prior_causes=["房价太高", "工资不涨"])
    assert "先验预期原因" in dossier
    assert "房价太高" in dossier


def test_core_variables_by_degree():
    state = _converged_state()
    core = core_variables(state, k=2)
    # c_001 度数 3 (两出一入) > d_001 度数 1
    assert core[0] == "c_001"
    assert "d_001" in core


def test_core_variables_excludes_decayed():
    state = _converged_state()
    decayed = state.graph.nodes["d_001"].model_copy(
        update={"lifecycle_state": "decayed"}
    )
    state.graph.replace_node("d_001", decayed)
    assert "d_001" not in core_variables(state)


def test_graph_stats():
    stats = graph_stats(_converged_state())
    assert stats["nodes_total"] == 5
    assert stats["nodes_l0"] == 3
    assert stats["nodes_l1"] == 1
    assert stats["nodes_l2"] == 1
    assert stats["edges"] == 3


# ─── narrative ─────────────────────────────────────────────


def test_generate_report_sends_dossier_and_instructions():
    import asyncio

    llm = FakeLLM()
    state = _converged_state()
    text = asyncio.run(generate_report(state, llm))
    assert text == "# 解释报告: 测试"
    assert llm.call_count == 1
    sent = llm.last_messages[0].content
    # prompt 必须同时包含硬约束指令与完整档案
    assert REPORT_INSTRUCTIONS[:20] in sent
    assert "长期收入预期恶化" in sent
    assert "p_001" in sent
