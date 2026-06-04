"""tests/web 共享 fixture."""
import pytest


@pytest.fixture(autouse=True)
def _reset_sid_locks():
    """每个 web test 前清空 per-sid 锁表.

    sse._locks 是进程级 dict, asyncio.Lock 绑定首次 await 的 event loop。
    TestClient 每请求可能起新 loop, 跨 test 复用同一锁会抛 'Future attached
    to a different loop'。清表保证隔离 (生产单 loop 无此问题)。
    """
    from explain_engine.web import sse
    sse._locks.clear()
    yield
    sse._locks.clear()
