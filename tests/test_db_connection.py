import pytest

from explain_agent.config import get_settings
from explain_agent.db.mysql import get_engine
from explain_agent.db.clickhouse import get_client as get_ch_client
from explain_agent.db.qdrant import get_qdrant_client


@pytest.mark.integration
def test_mysql_quant_db_reachable():
    engine = get_engine("quant")
    with engine.connect() as conn:
        result = conn.exec_driver_sql("SELECT 1").scalar()
        assert result == 1


@pytest.mark.integration
def test_clickhouse_reachable():
    client = get_ch_client()
    result = client.query("SELECT 1").result_rows
    assert result[0][0] == 1


@pytest.mark.integration
def test_qdrant_reachable():
    client = get_qdrant_client()
    info = client.get_collections()
    assert info is not None
