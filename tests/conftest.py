"""pytest fixtures for explain_engine tests.

约定：
- 不写测试 import 应用代码（避免 fixture 与 production code 互依赖）
- LLM provider 测试 mock，集成测放 @pytest.mark.integration
- session 落地用 tmp_path，绝不污染 sessions/ 或 ~/.explain/

Phase 9: autouse `isolated_explain_home` fixture 替老 SESSIONS_DIR fixture
pattern. 所有 test 透明 isolation 到 tmp_path/.explain (project_id=test_proj).

Phase 17.1: 顶部 load_dotenv() 让 EXPLAIN_TEST_DB_URL 等 .env 配置自动生效,
test 不依赖 user shell export — subagent / CI / pytest 单独跑都 work.
"""

# Phase 17.1: load .env 文件让 EXPLAIN_TEST_DB_URL 等环境变量在 test 中可用.
# 必须在 import pytest 之前 — fixture decorator 求值时即读 env.
from dotenv import load_dotenv

load_dotenv()

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_explain_home(tmp_path, monkeypatch):
    """Phase 9: All tests auto-isolate EXPLAIN_HOME to tmp_path/.explain.

    Replaces Phase 0-8 SESSIONS_DIR fixture pattern (now obsolete).
    Tests 不再需要手 set SESSIONS_DIR 或 EXPLAIN_HOME.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_proj")
    yield


@pytest.fixture
def tmp_sessions_dir(tmp_path, monkeypatch):
    """向后兼容老测试 fixture: 返回 tmp_path/sessions (mkdir).

    Phase 9 起 SessionStore 不再读 SESSIONS_DIR (走 EXPLAIN_HOME instead),
    但部分老测试依然显式传 directory 参数 / 用此 fixture 作 path container.
    保留方便老测试 minimal 改动 (SESSIONS_DIR setenv 也保留, 但已无 production
    effect).
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    return sessions_dir


@pytest.fixture(autouse=True)
def _disable_embedding_unless_marked(request, monkeypatch):
    """Phase 13 default: EXPLAIN_EMBEDDING_DISABLED=1 unless @pytest.mark.embedding.

    Prevents tests that touch lexicon flush from accidentally loading the
    4.3 GB BGE-M3 model. Tests that need real embedding must declare
    @pytest.mark.embedding marker, which opts them out of this disable.
    """
    if request.node.get_closest_marker("embedding") is None:
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
    else:
        # Explicit unset — tests marked @embedding should NOT inherit env from outer shell
        monkeypatch.delenv("EXPLAIN_EMBEDDING_DISABLED", raising=False)


@pytest.fixture
def mock_llm_response():
    """返回一个工具函数：给定 JSON dict，生成 mock LLM response。"""

    def _make(payload: dict, raw_text: str | None = None):
        from explain_engine.llm.client import Response

        return Response(
            text=raw_text if raw_text is not None else "",
            parsed=payload,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    return _make


# ── Phase 17.1 Wave 1: 远程 explain_test database (user 自部署 in 172.30.26.12) ──
#
# 设计修正 (2026-05-26): 撤 testcontainers — user 单人 dev 已有远程 PG, 本机
# spin container 是 over-engineering. 改用同 PG instance 不同 database (production
# = 'explain', test = 'explain_test', 物理隔离).
#
# 用户需在 172.30.26.12 跑一次:
#   ssh user@172.30.26.12 'docker exec explain-postgres psql -U explain -c "CREATE DATABASE explain_test"'
#   ssh user@172.30.26.12 'docker exec -i explain-postgres psql -U explain -d explain_test' < deploy/postgres/init/01-init.sql
#
# 然后 .env 加 EXPLAIN_TEST_DB_URL=postgresql://explain:<密码>@172.30.26.12:5432/explain_test
#
# 没设 EXPLAIN_TEST_DB_URL 时, lexicon_pg test 自动 SKIP (既有 1135 test 不挂).


@pytest.fixture(scope="session")
def pg_test_dsn():
    """Phase 17.1: 远程 explain_test database DSN, 读自 EXPLAIN_TEST_DB_URL env.

    未设环境变量时返 None (Test 标 @_skip_no_test_db 自动 skip).
    设了即返 dsn, 允许 reset_pg fixture TRUNCATE.
    """
    import os
    return os.environ.get("EXPLAIN_TEST_DB_URL")


@pytest.fixture
async def reset_pg(pg_test_dsn, monkeypatch):
    """Phase 17.1: TRUNCATE per test 跨 test 隔离 + 设 EXPLAIN_DB_URL = test dsn.

    NOT autouse — 显式 usefixtures 才触发, 既有 1135 test 不触本 fixture.

    lexicon_pg test 标 @pytest.mark.usefixtures("reset_pg") (class 或 function 级).

    **async fixture (Wave 2 修正)**: teardown 需 await pool.close() 显式关
    psycopg_pool 的 background workers (num_workers=3), 否则 pytest-asyncio
    function-scoped event loop teardown 时 workers 还活着, loop.close() hang.

    **3 层安全 guard 防误清生产数据**:
    1. pg_test_dsn 必须存在 (None 时 skip, 即 EXPLAIN_TEST_DB_URL 未设)
    2. dsn 必须含 '_test' 子串 (database 名应是 explain_test, 不能是 explain)
    3. dsn 不能等于 EXPLAIN_DB_URL (production)
    """
    import os

    import psycopg

    if pg_test_dsn is None:
        pytest.skip("EXPLAIN_TEST_DB_URL not set — lexicon_pg test 需远程 explain_test db")

    # Guard 2: dsn 必须含 '_test'
    assert "_test" in pg_test_dsn, (
        f"reset_pg TRUNCATE 拒绝在非 test database 上跑 (got dsn={pg_test_dsn!r}). "
        "EXPLAIN_TEST_DB_URL 应指向 explain_test 库, 不能是生产 explain 库."
    )
    # Guard 3: 跟 production DSN 区分
    prod_dsn = os.environ.get("EXPLAIN_DB_URL")
    assert pg_test_dsn != prod_dsn, (
        "reset_pg TRUNCATE 拒绝在生产 DSN 上跑 "
        "(EXPLAIN_TEST_DB_URL 跟 EXPLAIN_DB_URL 不能相同)."
    )

    monkeypatch.setenv("EXPLAIN_DB_URL", pg_test_dsn)
    with psycopg.connect(pg_test_dsn, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE variables, lexicon_merge_audit; "
            "UPDATE lexicon_meta SET flush_count_since = 0, last_retro_dedup_at = NULL "
            "WHERE id = 1"
        )
    yield
    # 清 module-level pool — 先 await close() 让 workers 退出, 再清引用
    try:
        from explain_engine.persistence import lexicon_pg

        if lexicon_pg._async_pool is not None:
            await lexicon_pg._async_pool.close()
            lexicon_pg._async_pool = None
        if lexicon_pg._sync_pool is not None:
            lexicon_pg._sync_pool.close()
            lexicon_pg._sync_pool = None
    except ImportError:
        pass  # Wave 2 之前 lexicon_pg 还没建
