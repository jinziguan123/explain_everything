"""Settings (pydantic-settings) + make_client factory。"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from explain_engine.llm.claude import ClaudeClient
from explain_engine.llm.client import LLMClient
from explain_engine.llm.deepseek import DeepSeekClient
from explain_engine.llm.openai_client import OpenAIClient

Provider = Literal["claude", "openai", "deepseek"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Provider = "claude"
    llm_model: str = "claude-opus-4-7"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"

    default_budget: int = Field(default=20, ge=1)
    sessions_dir: Path = Path("./sessions")


def make_client(settings: Settings) -> LLMClient:
    match settings.llm_provider:
        case "claude":
            if not settings.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY required for provider=claude")
            return ClaudeClient(
                api_key=settings.anthropic_api_key,
                default_model=settings.llm_model,
            )
        case "openai":
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY required for provider=openai")
            return OpenAIClient(
                api_key=settings.openai_api_key,
                default_model=settings.llm_model,
            )
        case "deepseek":
            if not settings.deepseek_api_key:
                raise ValueError("DEEPSEEK_API_KEY required for provider=deepseek")
            return DeepSeekClient(
                api_key=settings.deepseek_api_key,
                default_model=settings.llm_model,
                base_url=settings.deepseek_base_url,
            )
