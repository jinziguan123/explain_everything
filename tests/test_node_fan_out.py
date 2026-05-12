import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node


@pytest.mark.asyncio
async def test_fan_out_runs_all_dimensions():
    framework = {
        "dimensions": [
            {"id": f"d{i}", "name": f"D{i}", "data_sources": ["x"], "query_template": "t"}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 1, "soft_terminate_no_gain_rounds": 1, "max_concurrency": 3},
    }
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["framework"] = framework
    state["market_facts"] = {"snippet": ""}

    fake_worker_factory = MagicMock()
    fake_worker_instance = MagicMock()
    fake_worker_instance.run = AsyncMock(return_value=DimensionResult(
        evidence=[], mini_summary="", retry_count=1, no_data=True, confidence="low",
    ))
    fake_worker_factory.return_value = fake_worker_instance

    out = await fan_out_dimensions_node(state, worker_factory=fake_worker_factory)
    assert set(out["dimension_results"].keys()) == {"d0", "d1", "d2", "d3", "d4", "d5"}


@pytest.mark.asyncio
async def test_fan_out_semaphore_limits_concurrency():
    framework = {
        "dimensions": [
            {"id": f"d{i}", "name": f"D{i}", "data_sources": [], "query_template": "t"}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 1, "soft_terminate_no_gain_rounds": 1, "max_concurrency": 2},
    }
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["framework"] = framework
    state["market_facts"] = {}

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def slow_run(*args, **kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return DimensionResult(
            evidence=[], mini_summary="", retry_count=1, no_data=True, confidence="low",
        )

    fake_worker_factory = MagicMock()
    fake_worker = MagicMock()
    fake_worker.run = slow_run
    fake_worker_factory.return_value = fake_worker

    await fan_out_dimensions_node(state, worker_factory=fake_worker_factory)
    assert peak <= 2


@pytest.mark.asyncio
async def test_fan_out_isolates_dimension_failure():
    """1 个 worker 抛异常时其他 5 个继续完成, 失败维度落 no_data=True。"""
    from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node
    from explain_agent.graph.state import new_attribution_state, DimensionResult
    from unittest.mock import AsyncMock, MagicMock

    framework = {
        "dimensions": [
            {"id": f"dim_{i}", "name": f"维{i}", "data_sources": []}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 10, "max_concurrency": 6},
    }

    call_counter = {"n": 0}

    def make_worker(dimension_config, worker_config):
        worker = MagicMock()
        async def fake_run(**kw):
            call_counter["n"] += 1
            if dimension_config["id"] == "dim_2":
                raise RuntimeError("故意挂掉")
            return DimensionResult(
                evidence=[], mini_summary=f"ok {dimension_config['id']}",
                retry_count=1, no_data=False, confidence="medium",
            )
        worker.run = fake_run
        return worker

    state = new_attribution_state("test")
    state["framework"] = framework
    state["target"] = "X"
    state["time_window"] = None
    state["market_facts"] = {}

    out = await fan_out_dimensions_node(state, worker_factory=make_worker)
    assert len(out["dimension_results"]) == 6
    assert out["dimension_results"]["dim_2"]["no_data"] is True
    assert "故意挂掉" in out["dimension_results"]["dim_2"]["mini_summary"]
    for i in [0, 1, 3, 4, 5]:
        assert out["dimension_results"][f"dim_{i}"]["no_data"] is False


@pytest.mark.asyncio
async def test_fan_out_runs_dimensions_concurrently():
    """6 维 worker 各 sleep 0.5s, 总耗时必须 < 1.5s（真并发）, 而非 3s（串行）。"""
    import asyncio, time
    from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node
    from explain_agent.graph.state import new_attribution_state, DimensionResult

    sleep_per_dim = 0.5
    framework = {
        "dimensions": [
            {"id": f"dim_{i}", "name": f"维{i}", "data_sources": []}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 10, "max_concurrency": 6},
    }

    def make_worker(dimension_config, worker_config):
        class FakeWorker:
            async def run(self, target, time_window, market_facts):
                await asyncio.sleep(sleep_per_dim)
                return DimensionResult(
                    evidence=[], mini_summary="ok",
                    retry_count=1, no_data=False, confidence="medium",
                )
        return FakeWorker()

    state = new_attribution_state("test")
    state["framework"] = framework
    state["target"] = "X"
    state["time_window"] = None
    state["market_facts"] = {}

    t0 = time.perf_counter()
    out = await fan_out_dimensions_node(state, worker_factory=make_worker)
    elapsed = time.perf_counter() - t0

    assert len(out["dimension_results"]) == 6
    # 真并发应在 sleep_per_dim ~= 0.5s 略多一点完成
    # 这里给到 1.5s 是宽容上限（CI 慢时也能过），但远小于串行的 3s
    assert elapsed < sleep_per_dim * 3, (
        f"fan_out 看起来是串行运行的: 耗时 {elapsed:.2f}s, "
        f"预期 < {sleep_per_dim * 3:.2f}s。检查 worker 是否真 async, "
        f"以及 LLM client 是否用了 await achat。"
    )
