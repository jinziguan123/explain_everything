from datetime import datetime, date
from typing import Literal, Protocol, runtime_checkable
from pydantic import BaseModel, Field


SourceType = Literal["news", "policy", "research", "market_data", "capital_flow"]


class Evidence(BaseModel):
    id: str
    source: str
    source_type: SourceType
    url: str | None = None
    title: str | None = None
    snippet: str
    raw_payload: dict | None = None
    snapshot_id: str | None = None
    timestamp: datetime
    metadata: dict = Field(default_factory=dict)


class AdapterQuery(BaseModel):
    keywords: list[str]
    time_window: tuple[date, date]
    target: str
    filters: dict = Field(default_factory=dict)
    limit: int = 50


@runtime_checkable
class DataAdapter(Protocol):
    name: str

    async def query(self, q: AdapterQuery) -> list[Evidence]: ...
