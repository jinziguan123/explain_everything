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
) -> list[VariableNode]:
    """调 variable_extraction prompt 生 concrete phenomena。

    LLM 出 {name, description}，系统补 id / level / confidence / epistemic
    default。截断到 max_count 条。

    Raises:
        SchemaValidationError: LLM 未返回 parsed 内容
    """
    prompt = load_prompt("variable_extraction")
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=question,
                min_count=min_count,
                max_count=max_count,
            ),
        ),
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
