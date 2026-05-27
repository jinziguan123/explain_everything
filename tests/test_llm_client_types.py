"""LLMClient Protocol + 基本类型 test。

LLMClient 是 Protocol，不直接测；测 Message / Response 数据类。
"""

import pytest
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import LLMClient, Message, Response


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


class TestMessage:
    def test_user_message(self):
        m = Message(role="user", content="why?")
        assert m.role == "user"

    def test_role_validation(self):
        with pytest.raises(ValidationError):
            Message(role="invalid", content="x")  # type: ignore[arg-type]

    def test_system_user_assistant_roles_accepted(self):
        for role in ("system", "user", "assistant"):
            Message(role=role, content="x")  # type: ignore[arg-type]


class TestResponse:
    def test_response_with_parsed(self):
        r = Response(
            text='{"answer": "yes", "confidence": 0.9}',
            parsed={"answer": "yes", "confidence": 0.9},
            model="claude-opus",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        assert r.parsed["answer"] == "yes"
        assert r.usage["input_tokens"] == 100

    def test_response_text_only(self):
        r = Response(text="hello", parsed=None, model="x", usage={})
        assert r.parsed is None

    def test_response_reasoning_field_default_none(self):
        """Phase 19 Task 1: 老 caller 不传 reasoning → default None (backward compat)."""
        r = Response(text="hi", parsed=None, model="m", usage={})
        assert r.reasoning is None

    def test_response_reasoning_field_explicit(self):
        """Phase 19 Task 1: caller 传 reasoning → 保留."""
        r = Response(
            text="hi",
            reasoning="思考过程",
            parsed=None,
            model="m",
            usage={},
        )
        assert r.reasoning == "思考过程"


class TestLLMClientIsProtocol:
    """LLMClient 必须可作为 Protocol 标注，不必实例化。"""

    def test_protocol_can_be_used_as_type(self):
        def takes_client(c: LLMClient) -> str:
            return "ok"

        # 至少不能在 import / 函数定义阶段崩
        assert takes_client.__annotations__["c"] is LLMClient
