from functools import lru_cache

from explain_agent.config import get_settings
from explain_agent.llm.client import LLMClient, make_llm_client


@lru_cache
def get_weak_llm() -> LLMClient:
    return make_llm_client(get_settings().weak_llm)


@lru_cache
def get_strong_llm() -> LLMClient:
    return make_llm_client(get_settings().strong_llm)


__all__ = ["LLMClient", "make_llm_client", "get_weak_llm", "get_strong_llm"]
