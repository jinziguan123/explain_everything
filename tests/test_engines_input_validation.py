"""Wave 3 Task 3.1: input_validation engine.

design §6.2: validate(question, l0_nodes, llm) → InputAlignmentReport.
注意 validate 只返 report, 不抛 InsufficientObservationsError (CLI 决定).
"""

import pytest

from explain_engine.engines.errors import (
    CognitiveEngineError,
    InsufficientObservationsError,
)
from explain_engine.engines.input_validation import (
    MIN_OVERLAP_SCORE,
    InputAlignmentReport,
    validate,
)
from explain_engine.schema.nodes import VariableNode


def _l0(name: str, desc: str = "d") -> VariableNode:
    return VariableNode(
        id=f"p_{name}", name=name, description=desc,
        abstraction_level=0, confidence=0.7, epistemic="observation",
    )


class _FakeLLMOutput:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeLLM:
    def __init__(self, response):
        self.response = response

    async def chat(self, messages, schema):
        return _FakeLLMOutput(parsed=self.response)


class _FakeLLMSeq:
    """Returns successive responses on each .chat() call.

    Use to test retry paths (None parsed → retry success / Pydantic error → retry success / both fail).
    """
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, schema):
        if self.calls >= len(self.responses):
            raise IndexError(f"FakeLLMSeq exhausted at call {self.calls}")
        r = self.responses[self.calls]
        self.calls += 1
        return _FakeLLMOutput(parsed=r)


class TestValidate:
    @pytest.mark.asyncio
    async def test_high_overlap_returns_high_score(self) -> None:
        llm = _FakeLLM({
            "question_subject": "员工 A 的离职原因",
            "observation_subjects": ["员工 A 的工作时长", "员工 A 的会议反馈"],
            "overlap_score": 5,
            "falsifiable_reason": "observations 直接关于员工 A 的工作行为, 高对齐.",
        })
        report = await validate("为什么员工 A 离职?", [_l0("o1"), _l0("o2")], llm)
        assert isinstance(report, InputAlignmentReport)
        assert report.overlap_score == 5
        assert report.question_subject == "员工 A 的离职原因"

    @pytest.mark.asyncio
    async def test_low_overlap_returns_low_score(self) -> None:
        llm = _FakeLLM({
            "question_subject": "员工 A 的离职原因",
            "observation_subjects": ["公司股价", "市场总指数"],
            "overlap_score": 1,
            "falsifiable_reason": "observations 关于宏观市场, 与员工个体行为无直接关系.",
        })
        report = await validate("为什么员工 A 离职?", [_l0("o1"), _l0("o2")], llm)
        assert report.overlap_score == 1

    @pytest.mark.asyncio
    async def test_returns_falsifiable_reason_always(self) -> None:
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": ["Y"],
            "overlap_score": 4,
            "falsifiable_reason": "explanation regardless",
        })
        report = await validate("q", [_l0("o")], llm)
        assert report.falsifiable_reason == "explanation regardless"

    @pytest.mark.asyncio
    async def test_validate_does_not_raise_insufficient_obs_error(self) -> None:
        """validate 永远只返 report, 不抛 InsufficientObservationsError (CLI 决定)."""
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": ["Y"],
            "overlap_score": 0,
            "falsifiable_reason": "complete mismatch",
        })
        # 即使 score=0 也只是返 report
        report = await validate("q", [_l0("o")], llm)
        assert report.overlap_score == 0

    @pytest.mark.asyncio
    async def test_invalid_overlap_score_raises_schema_error(self) -> None:
        from explain_engine.llm.errors import SchemaValidationError
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": [],
            "overlap_score": 99,  # 不在 0-5
            "falsifiable_reason": "r",
        })
        with pytest.raises(SchemaValidationError):
            await validate("q", [_l0("o")], llm)

    @pytest.mark.asyncio
    async def test_empty_l0_handled(self) -> None:
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": [],
            "overlap_score": 0,
            "falsifiable_reason": "no observations provided",
        })
        report = await validate("q", [], llm)
        assert report.overlap_score == 0

    @pytest.mark.asyncio
    async def test_retry_after_none_parsed_succeeds(self) -> None:
        """First call returns None parsed → retry → success."""
        valid = {
            "question_subject": "X",
            "observation_subjects": ["Y"],
            "overlap_score": 3,
            "falsifiable_reason": "ok",
        }
        llm = _FakeLLMSeq([None, valid])
        report = await validate("q", [_l0("o")], llm)
        assert report.overlap_score == 3
        assert llm.calls == 2

    @pytest.mark.asyncio
    async def test_retry_after_invalid_schema_succeeds(self) -> None:
        """First call returns invalid schema → retry → success."""
        invalid = {"overlap_score": 99}  # missing required + bad range
        valid = {
            "question_subject": "X",
            "observation_subjects": [],
            "overlap_score": 2,
            "falsifiable_reason": "ok",
        }
        llm = _FakeLLMSeq([invalid, valid])
        report = await validate("q", [_l0("o")], llm)
        assert report.overlap_score == 2
        assert llm.calls == 2

    @pytest.mark.asyncio
    async def test_two_failures_raises_schema_error(self) -> None:
        """Both attempts fail → SchemaValidationError raised after 2 calls."""
        from explain_engine.llm.errors import SchemaValidationError
        llm = _FakeLLMSeq([None, None])
        with pytest.raises(SchemaValidationError):
            await validate("q", [_l0("o")], llm)
        assert llm.calls == 2


class TestInsufficientObservationsError:
    def test_error_str_format(self) -> None:
        err = InsufficientObservationsError(
            overlap_score=1,
            question_subject="员工 A 的离职原因",
            observation_subjects=["公司股价"],
            falsifiable_reason="observations 关于股价, question 问员工",
        )
        s = str(err)
        assert "1/5" in s
        assert "员工" in s
        assert "股价" in s

    def test_error_inherits_cognitive_engine_error(self) -> None:
        err = InsufficientObservationsError(
            overlap_score=0, question_subject="X",
            observation_subjects=[], falsifiable_reason="r",
        )
        assert isinstance(err, CognitiveEngineError)


class TestMinOverlapScore:
    def test_constant_value(self) -> None:
        # 文档声明 MIN_OVERLAP_SCORE = 2 (< 2 即 0/1 触发 fail)
        assert MIN_OVERLAP_SCORE == 2
