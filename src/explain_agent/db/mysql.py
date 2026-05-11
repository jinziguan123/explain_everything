from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from explain_agent.config import get_settings


@lru_cache
def get_engine(db: str = "quant") -> Engine:
    s = get_settings()
    if db == "quant":
        url = s.mysql_quant_url()
    elif db == "explain":
        url = s.mysql_explain_url()
    else:
        raise ValueError(f"unknown db: {db}")
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)
