from typing import Protocol

from anthropic import Anthropic
from openai import OpenAI

from explain_agent.config import LLMConfig


class LLMClient(Protocol):
    cfg: LLMConfig

    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str: ...


class AnthropicClient:
    def __init__(self, cfg: LLMConfig, sdk_client=None):
        self.cfg = cfg
        self.sdk = sdk_client or Anthropic(api_key=cfg.api_key, base_url=cfg.base_url)

    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = self.sdk.messages.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            text = getattr(block, "text", None)
            if text is not None:
                return text
        return ""


class OpenAIClient:
    def __init__(self, cfg: LLMConfig, sdk_client=None):
        self.cfg = cfg
        self.sdk = sdk_client or OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = self.sdk.chat.completions.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


def make_llm_client(cfg: LLMConfig) -> LLMClient:
    if cfg.protocol == "anthropic":
        return AnthropicClient(cfg)
    if cfg.protocol == "openai":
        return OpenAIClient(cfg)
    raise ValueError(f"unknown protocol: {cfg.protocol}")
