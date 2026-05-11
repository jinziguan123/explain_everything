from functools import lru_cache

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from explain_agent.config import get_settings


@lru_cache
def get_client() -> Client:
    s = get_settings()
    return clickhouse_connect.get_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_db,
    )
