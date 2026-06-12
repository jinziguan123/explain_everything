"""预测自动起草测试 — 机器提案、人签字 (Phase A)。

FakeLLM + stub storage, 不触网 / 不触 PG / 不加载 BGE。
"""

import asyncio

from explain_engine.engines.theory.cache import TheoriesCache, _atomic_write_cache
from explain_engine.engines.theory.ledger import add_prediction, load_ledger
from explain_engine.engines.theory.prediction_draft import (
    auto_draft_predictions,
    draft_for_theory,
)
from explain_engine.engines.theory.theory import Theory
from explain_engine.llm.client import Response


class StubStorage:
    def __init__(self, p):
        self._p = p

    def knowledge_dir(self):
        return self._p


def _theory(tid="t_aaaaaaaaaa"):
    return Theory(
        id=tid, motif_type="chain", theme_ids=(), node_ids=("v_a", "v_b"),
        edges=(("v_a", "v_b", "causes"),), supporting_sessions=("s_1", "s_2"),
        natural_language_summary="不确定性 → 风险规避", structure_complexity=2,
        first_seen_session="s_1", last_seen_session="s_2",
    )


class FakeDraftLLM:
    def __init__(self, predictions):
        self._predictions = predictions
        self.call_count = 0

    async def chat(self, messages, schema=None, model=None, on_delta=None):
        self.call_count += 1
        return Response(
            text="", parsed={"predictions": self._predictions}, model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


GOOD_ITEMS = [
    {"assertion": "2026 年内主要消费品类的以旧换新补贴对销量拉动持续低于预期",
     "method": "search", "deadline": None},
    {"assertion": "未来 6 个月居民新增存款同比增速保持正值",
     "method": "time_window", "deadline": "2026-12-01"},
]


def _write_cache(storage, theories):
    cache = TheoriesCache(tentative_theories=list(theories))
    _atomic_write_cache(cache, storage.knowledge_dir() / "theories.json")


# ─── draft_for_theory ──────────────────────────────────────


def test_draft_returns_valid_items():
    items = asyncio.run(draft_for_theory(_theory(), FakeDraftLLM(GOOD_ITEMS)))
    assert len(items) == 2
    assert items[1].method == "time_window"


def test_draft_drops_invalid_items():
    bad = [
        {"assertion": "未来半年内某事将发生但没给期限",
         "method": "time_window", "deadline": None},          # 缺 deadline
        {"assertion": "这一条的日期格式是坏的, 应当被丢弃掉",
         "method": "time_window", "deadline": "2026/12/01"},  # 格式错
        {"assertion": "这一条是合法的检索类断言, 应当保留下来",
         "method": "search", "deadline": None},
    ]
    items = asyncio.run(draft_for_theory(_theory(), FakeDraftLLM(bad)))
    assert len(items) == 1
    assert items[0].method == "search"


def test_draft_caps_at_two():
    many = [
        {"assertion": f"第 {i} 条足够长度的可检验断言内容示例",
         "method": "search", "deadline": None}
        for i in range(5)
    ]
    items = asyncio.run(draft_for_theory(_theory(), FakeDraftLLM(many)))
    assert len(items) == 2


# ─── auto_draft_predictions ────────────────────────────────


def test_auto_draft_registers_with_llm_origin(tmp_path):
    storage = StubStorage(tmp_path)
    _write_cache(storage, [_theory()])
    llm = FakeDraftLLM(GOOD_ITEMS)

    drafted = asyncio.run(auto_draft_predictions(storage, llm))
    assert len(drafted) == 2
    persisted = load_ledger(storage)
    assert all(p.origin == "llm" for p in persisted)
    assert all(p.status == "pending" for p in persisted)  # 结算留给人


def test_auto_draft_skips_theories_with_existing_ledger(tmp_path):
    storage = StubStorage(tmp_path)
    _write_cache(storage, [_theory()])
    add_prediction(storage, "t_aaaaaaaaaa", "已有人工登记的预测断言示例")

    llm = FakeDraftLLM(GOOD_ITEMS)
    drafted = asyncio.run(auto_draft_predictions(storage, llm))
    assert drafted == []
    assert llm.call_count == 0  # 不浪费 LLM 调用


def test_auto_draft_idempotent(tmp_path):
    """第二次调用不重复起草 (每理论只机器提案一次)。"""
    storage = StubStorage(tmp_path)
    _write_cache(storage, [_theory()])
    llm = FakeDraftLLM(GOOD_ITEMS)
    asyncio.run(auto_draft_predictions(storage, llm))
    again = asyncio.run(auto_draft_predictions(storage, llm))
    assert again == []
    assert len(load_ledger(storage)) == 2


def test_auto_draft_single_theory_failure_not_fatal(tmp_path):
    storage = StubStorage(tmp_path)
    _write_cache(storage, [_theory("t_aaaaaaaaaa"), _theory("t_bbbbbbbbbb")])

    class FlakyLLM(FakeDraftLLM):
        async def chat(self, messages, schema=None, model=None, on_delta=None):
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("limit")
            return await super().chat(messages, schema=schema, model=model)

    drafted = asyncio.run(auto_draft_predictions(storage, FlakyLLM(GOOD_ITEMS)))
    # 一个理论失败, 另一个成功
    assert len(drafted) == 2
    assert {p.theory_id for p in drafted} == {"t_bbbbbbbbbb"}


def test_origin_roundtrip(tmp_path):
    storage = StubStorage(tmp_path)
    add_prediction(storage, "t_x", "人工登记的断言示例内容", origin="user")
    add_prediction(storage, "t_x", "机器起草的断言示例内容", origin="llm")
    loaded = load_ledger(storage)
    assert [p.origin for p in loaded] == ["user", "llm"]