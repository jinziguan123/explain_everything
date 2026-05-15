"""Phase 8 Wave 3: cognitive engine errors (fail-fast on input mismatch).

哲学锚点 §9.4 可证伪性: "Theory 必须可失败, 否则系统会神学化".
系统必须能说"我无法回答这个 question, 因为 observations 不匹配", 而非
强行编造 explanation.
"""

from __future__ import annotations


class CognitiveEngineError(Exception):
    """Phase 8 base for engine-level fail-fast errors."""


class InsufficientObservationsError(CognitiveEngineError):
    """Wave 3: question 与 L0 observations 不对齐, 无法形成 explanation.

    Raised by CLI (cli.py) — NOT by engine.input_validation.validate().
    Engine returns the report; CLI decides whether to fail-fast based on
    threshold + --no-input-check flag.

    Attributes:
        overlap_score: input_validation 给的 0-5 整数分.
        question_subject: LLM 识别出的 question 核心主体.
        observation_subjects: L0 observations 各自的主体.
        falsifiable_reason: LLM 给的'为什么不对齐'的明确理由.
    """

    def __init__(
        self,
        overlap_score: int,
        question_subject: str,
        observation_subjects: list[str],
        falsifiable_reason: str,
    ):
        self.overlap_score = overlap_score
        self.question_subject = question_subject
        self.observation_subjects = observation_subjects
        self.falsifiable_reason = falsifiable_reason
        super().__init__(
            f"Input alignment too low (score={overlap_score}/5). "
            f"Question 主体: {question_subject!r}; "
            f"Observation 主体: {observation_subjects!r}. "
            f"理由: {falsifiable_reason}"
        )
