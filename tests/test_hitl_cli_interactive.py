"""HITL 1 review_phenomena test."""

from collections.abc import Iterator

import pytest

from explain_engine.hitl.cli_interactive import review_phenomena
from explain_engine.schema.nodes import VariableNode


def _node(id_: str, name: str = "x", desc: str = "x") -> VariableNode:
    return VariableNode(
        id=id_,
        name=name,
        description=desc,
        abstraction_level=0,
        confidence=0.7,
        epistemic="observation",
    )


def _make_prompt_sequence(answers: list[str]) -> Iterator[str]:
    """生成 Prompt.ask 的固定回答序列。"""
    return iter(answers)


@pytest.fixture
def mock_prompts(monkeypatch):
    """提供一个工具：patch Prompt.ask 返回预设序列。

    用法：mock_prompts(["k", "k", "k"]) 表示连续 3 次 keep。
    Confirm.ask 走同一个 patch（True/False）。
    """

    def _setup(answers: list, confirm_answers: list[bool] | None = None):
        ask_iter = iter(answers)
        confirm_iter = iter(confirm_answers or [])

        def _ask(*args, **kwargs):
            return next(ask_iter)

        def _confirm(*args, **kwargs):
            try:
                return next(confirm_iter)
            except StopIteration:
                return False  # 默认 False (停止 add 循环)

        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.Prompt.ask", _ask
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.Confirm.ask", _confirm
        )

    return _setup


class TestReviewPhenomena:
    def test_keep_all(self, mock_prompts):
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        # 3 个 k 选择 + add 循环 False
        mock_prompts(answers=["k", "k", "k"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert len(result) == 3
        assert [n.id for n in result] == ["p_001", "p_002", "p_003"]

    def test_drop_one(self, mock_prompts):
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        # k, d, k
        mock_prompts(answers=["k", "d", "k"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert len(result) == 2
        assert [n.id for n in result] == ["p_001", "p_003"]

    def test_edit_one(self, mock_prompts):
        phenomena = [_node("p_001", name="old name", desc="old desc")]
        # e, "新名", "新描述", add 循环 False
        mock_prompts(answers=["e", "新名", "新描述"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert len(result) == 1
        assert result[0].name == "新名"
        assert result[0].description == "新描述"
        assert result[0].id == "p_001"  # id 不变

    def test_drop_all(self, mock_prompts):
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        mock_prompts(answers=["d", "d", "d"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert result == []

    def test_add_new_phenomena(self, mock_prompts):
        phenomena = [_node("p_001")]
        # keep 原 1 条 → 添加循环 True 1 次输入新名/描述 → False 退出
        mock_prompts(
            answers=["k", "新现象1", "描述1"],
            confirm_answers=[True, False],
        )

        result = review_phenomena(phenomena)

        assert len(result) == 2
        assert result[1].name == "新现象1"
        assert result[1].description == "描述1"
        # 用户加的 id 用 p_NNN 顺延 LLM 最大 +1
        assert result[1].id.startswith("p_")
        assert result[1].id[2:].isdigit()
        assert result[1].id == "p_002"
        assert result[1].source == "user"

    def test_add_skipped_when_empty_name(self, mock_prompts):
        phenomena = [_node("p_001")]
        # keep → 添加循环 True 1 次 → 空名 → 跳出
        mock_prompts(
            answers=["k", ""],
            confirm_answers=[True],
        )

        result = review_phenomena(phenomena)

        assert len(result) == 1
        assert result[0].id == "p_001"

    def test_user_added_phenomena_have_sequential_p_ids(self, mock_prompts):
        phenomena = [_node("p_001"), _node("p_002")]
        mock_prompts(
            answers=["k", "k", "u1", "d1", "u2", "d2"],
            confirm_answers=[True, True, False],
        )

        result = review_phenomena(phenomena)

        assert len(result) == 4
        # 顺延 LLM 最大 p_002 → p_003, p_004
        assert result[2].id == "p_003"
        assert result[3].id == "p_004"
        assert result[2].source == "user"
        assert result[3].source == "user"
