"""BootstrapEngine — Phase 3 入口。

调 variable_extraction prompt 生 8-15 个 concrete phenomena。
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.nodes import VariableNode

if TYPE_CHECKING:
    from explain_engine.engines.theory.theory import Theory


class _PhenomenonOutput(BaseModel):
    name: str
    description: str


class BootstrapOutput(BaseModel):
    """variable_extraction prompt 的 structured output schema。"""

    phenomena: list[_PhenomenonOutput]


async def bootstrap_phenomena(
    question: str,
    llm: LLMClient,
    min_count: int = 8,
    max_count: int = 15,
    lexicon: list[dict] | None = None,
    lexicon_top_k: int = 20,
    theories: "list[Theory] | None" = None,
) -> list[VariableNode]:
    """调 variable_extraction prompt 生 concrete phenomena。

    LLM 出 {name, description}，系统补 id / level / confidence / epistemic
    default。截断到 max_count 条。

    Phase 10 Wave 3: optional lexicon prior — 非空时 Top-K render 拼到 user
    message 末尾, 让 LLM 自主参考已知 abstractions (system msg 不动). lexicon
    =None/[] 时行为不变 (backward compat).

    Phase 16: optional theories prior — 非空时 prompt 加段引导 LLM 在已知 theory
    框架下生现象 (但允许偏离). JEPA prediction-as-loss + 哲学 §9.4 落地.
    theories=None/[] 时行为不变 (backward compat).

    Args:
        lexicon: knowledge/variables.json 的 "variables" list (or None).
        lexicon_top_k: 取 Top-K 进 prompt (默认 20, ~1.7k token).
        theories: 跨 session stable theory list (or None). 限 top-5 防 prompt 爆.

    Raises:
        SchemaValidationError: LLM 未返回 parsed 内容
    """
    prior_section = ""
    if lexicon:
        # 局部 import 避 circular (lexicon.py imports schema; bootstrap.py imports
        # schema; safe, 但局部 import 让依赖更显性).
        from explain_engine.engines.lexicon import (
            _render_lexicon_for_prompt,
            _select_top_k_vars,
        )
        top_k = _select_top_k_vars({"variables": lexicon}, k=lexicon_top_k)
        prior_section = _render_lexicon_for_prompt(top_k)

    # Phase 16: theories prior
    theories_section = ""
    if theories:
        theories_section = _build_theories_prompt_section(theories)

    prompt = load_prompt("variable_extraction")
    user_content = prompt["user_template"].format(
        question=question,
        min_count=min_count,
        max_count=max_count,
    )
    if prior_section:
        user_content = user_content + "\n\n" + prior_section
    if theories_section:
        user_content = user_content + "\n\n" + theories_section

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=user_content),
    ]
    resp = await llm.chat(messages, schema=BootstrapOutput)
    if resp.parsed is None:
        raise SchemaValidationError("LLM 未返回 structured output")

    raw = BootstrapOutput.model_validate(resp.parsed)
    phenomena = raw.phenomena[:max_count]

    return [
        VariableNode(
            id=f"p_{i + 1:03d}",
            name=p.name,
            description=p.description,
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
        )
        for i, p in enumerate(phenomena)
    ]


def _build_theories_prompt_section(theories: "list[Theory]") -> str:
    """JEPA (a) — 给 LLM 看你过去发现的 stable theory, 软引导不强制.

    限 top-5 防 prompt 爆. 软引导防过拟合.
    """
    if not theories:
        return ""

    lines = [
        "## 跨 session 已发现的稳定因果模式 (仅供参考, 不必强求新问题 match)",
        "",
        "你过去分析的多个 session 中, 以下因果模式反复出现:",
        "",
    ]
    for i, t in enumerate(theories[:5], start=1):
        # natural_language_summary 已是中文 (Task 12 _generate_summary 生成)
        line = (
            f"{i}. {t.natural_language_summary}"
            f" (出现 {len(t.supporting_sessions)} session"
        )
        if t.predictive_power > 0:
            line += f", 预测准确度 {t.predictive_power:.0%}"
        line += ")"
        lines.append(line)

    lines.extend([
        "",
        "**注意**:",
        "- 这些是历史 pattern, 新问题可能跟它们相关也可能完全无关",
        "- 若现象自然映射到上述模式, 可复用其涉及的术语 (e.g. \"风险规避\")",
        "- 不要强行套, 若新问题领域不同就忽略",
    ])
    return "\n".join(lines)
