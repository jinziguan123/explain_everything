from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProtocol = Literal["anthropic", "openai"]


class LLMConfig(BaseModel):
    protocol: LLMProtocol
    base_url: str
    api_key: str
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
    )

    mysql_host: str
    mysql_port: int = 3306
    mysql_user: str
    mysql_password: str
    mysql_quant_db: str = "quant_data"
    mysql_explain_db: str = "explain_agent"

    clickhouse_host: str
    clickhouse_port: int = 8123
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_db: str = "quant_data"

    qdrant_host: str
    qdrant_port: int = 6333
    qdrant_api_key: str | None = None

    weak_llm_protocol: LLMProtocol
    weak_llm_base_url: str
    weak_llm_api_key: str
    weak_llm_model: str

    strong_llm_protocol: LLMProtocol
    strong_llm_base_url: str
    strong_llm_api_key: str
    strong_llm_model: str

    snapshot_dir: Path = Path("./data/snapshots")
    embedding_cache_dir: Path = Path("./data/embeddings_cache")
    log_dir: Path = Path("./logs")

    @property
    def weak_llm(self) -> LLMConfig:
        return LLMConfig(
            protocol=self.weak_llm_protocol,
            base_url=self.weak_llm_base_url,
            api_key=self.weak_llm_api_key,
            model=self.weak_llm_model,
        )

    @property
    def strong_llm(self) -> LLMConfig:
        return LLMConfig(
            protocol=self.strong_llm_protocol,
            base_url=self.strong_llm_base_url,
            api_key=self.strong_llm_api_key,
            model=self.strong_llm_model,
        )

    def mysql_quant_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_quant_db}?charset=utf8mb4"
        )

    def mysql_explain_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_explain_db}?charset=utf8mb4"
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
