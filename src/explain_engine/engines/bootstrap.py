"""BootstrapEngine — Phase 3 入口。

调 variable_extraction prompt 生 8-15 个 concrete phenomena。
"""

from pydantic import BaseModel

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.nodes import VariableNode


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
) -> list[VariableNode]:
    """调 variable_extraction prompt 生 concrete phenomena。

    LLM 出 {name, description}，系统补 id / level / confidence / epistemic
    default。截断到 max_count 条。

    Phase 10 Wave 3: optional lexicon prior — 非空时 Top-K render 拼到 user
    message 末尾, 让 LLM 自主参考已知 abstractions (system msg 不动). lexicon
    =None/[] 时行为不变 (backward compat).

    Args:
        lexicon: knowledge/variables.json 的 "variables" list (or None).
        lexicon_top_k: 取 Top-K 进 prompt (默认 20, ~1.7k token).

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

    prompt = load_prompt("variable_extraction")
    user_content = prompt["user_template"].format(
        question=question,
        min_count=min_count,
        max_count=max_count,
    )
    if prior_section:
        user_content = user_content + "\n\n" + prior_section

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
