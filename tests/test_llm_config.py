"""Phase 20.4: LLM provider 配置管理 (llm_config store + config 集成 + /llm slash)."""
from __future__ import annotations

import json
import os
import stat

import pytest

from explain_engine.llm.llm_config import (
    LLMConfig,
    LLMConfigStore,
    LLMProfile,
)

# ─── 数据模型: main / light config 解析 ───

def test_profile_main_config():
    p = LLMProfile(
        name="ds", protocol="anthropic",
        base_url="https://x", api_key="sk-1", model="m-pro",
    )
    mc = p.main_config()
    assert mc["protocol"] == "anthropic"
    assert mc["base_url"] == "https://x"
    assert mc["api_key"] == "sk-1"
    assert mc["model"] == "m-pro"


def test_profile_light_falls_back_to_main_when_unset():
    p = LLMProfile(
        name="ds", protocol="anthropic",
        base_url="https://x", api_key="sk-1", model="m-pro",
    )
    lc = p.light_config()
    # light_* 全空 → 跟主完全一致
    assert lc["protocol"] == "anthropic"
    assert lc["base_url"] == "https://x"
    assert lc["api_key"] == "sk-1"
    assert lc["model"] == "m-pro"


def test_profile_light_overrides_per_field():
    p = LLMProfile(
        name="ds", protocol="anthropic",
        base_url="https://x", api_key="sk-1", model="m-pro",
        light_model="m-flash", light_base_url="https://y",
    )
    lc = p.light_config()
    assert lc["model"] == "m-flash"       # 覆盖
    assert lc["base_url"] == "https://y"  # 覆盖
    assert lc["api_key"] == "sk-1"        # 未设 → fallback 主
    assert lc["protocol"] == "anthropic"  # 未设 → fallback 主


# ─── store: load/save/upsert/delete/set_active ───

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    return LLMConfigStore()


def test_load_missing_file_returns_empty(store):
    cfg = store.load()
    assert isinstance(cfg, LLMConfig)
    assert cfg.active is None
    assert cfg.profiles == {}


def test_upsert_first_profile_auto_activates(store):
    p = LLMProfile(name="a", base_url="https://x", api_key="k", model="m")
    cfg = store.upsert(p)
    assert cfg.active == "a"  # 首个自动激活
    assert "a" in cfg.profiles


def test_upsert_second_does_not_steal_active(store):
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    cfg = store.upsert(
        LLMProfile(name="b", base_url="https://y", api_key="k2", model="m2")
    )
    assert cfg.active == "a"  # 第二个不抢 active
    assert set(cfg.profiles) == {"a", "b"}


def test_upsert_activate_flag(store):
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    cfg = store.upsert(
        LLMProfile(name="b", base_url="https://y", api_key="k2", model="m2"),
        activate=True,
    )
    assert cfg.active == "b"


def test_set_active_unknown_raises(store):
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    with pytest.raises(KeyError):
        store.set_active("nope")


def test_delete_active_reassigns(store):
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    store.upsert(LLMProfile(name="b", base_url="https://y", api_key="k2", model="m2"))
    store.set_active("a")
    cfg = store.delete("a")
    assert cfg.active == "b"  # 删 active → 切到剩余
    assert "a" not in cfg.profiles


def test_delete_last_profile_active_none(store):
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    cfg = store.delete("a")
    assert cfg.active is None
    assert cfg.profiles == {}


def test_save_sets_0600_perms(store):
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600


def test_corrupt_file_load_returns_empty(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not valid json", encoding="utf-8")
    cfg = store.load()  # 不抛 — 损坏返空 (fallback env)
    assert cfg.profiles == {}
    assert cfg.active is None


def test_roundtrip_persists(store):
    store.upsert(
        LLMProfile(
            name="ds", protocol="openai", base_url="https://x",
            api_key="sk-1", model="gpt", max_tokens=8000,
            structured_output_mode="json_object",
        )
    )
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["active"] == "ds"
    assert raw["profiles"]["ds"]["protocol"] == "openai"
    assert raw["profiles"]["ds"]["max_tokens"] == 8000
    # 重读
    cfg2 = LLMConfigStore().load()
    assert cfg2.active_profile().model == "gpt"


# ─── config.py 集成: profile 优先 + env fallback ───

def _clear_llm_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)


def test_make_llm_client_uses_active_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    _clear_llm_env(monkeypatch)
    LLMConfigStore().upsert(
        LLMProfile(
            name="ds", protocol="anthropic",
            base_url="https://api.deepseek.com/anthropic",
            api_key="sk-x", model="deepseek-v4-pro",
            light_model="deepseek-v4-flash",
        )
    )
    from explain_engine.config import make_light_llm_client, make_llm_client

    main = make_llm_client()
    light = make_light_llm_client()
    assert type(main).__name__ == "AnthropicProtocolClient"
    assert main._default_model == "deepseek-v4-pro"
    # light_model 覆盖生效
    assert light._default_model == "deepseek-v4-flash"


def test_make_llm_client_openai_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    _clear_llm_env(monkeypatch)
    LLMConfigStore().upsert(
        LLMProfile(
            name="oai", protocol="openai", base_url="https://api.openai.com/v1",
            api_key="sk-o", model="gpt-4o", structured_output_mode="json_object",
        )
    )
    from explain_engine.config import make_llm_client

    c = make_llm_client()
    assert type(c).__name__ == "OpenAIProtocolClient"
    assert c._default_model == "gpt-4o"


def test_make_llm_client_falls_back_to_env_when_no_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))  # 空, 无 llm_config.json
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://env-host")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    from explain_engine.config import make_llm_client

    c = make_llm_client()
    assert c._default_model == "env-model"  # 走 env 路径


# ─── /llm slash handler ───

@pytest.mark.asyncio
async def test_slash_llm_show_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    from explain_engine.chat.slash_commands import _handle_llm

    events = await _handle_llm(None, ["show"])
    assert len(events) == 1
    assert events[0].type == "slash_llm"
    assert ".env" in events[0].content


@pytest.mark.asyncio
async def test_slash_llm_show_lists_profiles_masked(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    LLMConfigStore().upsert(
        LLMProfile(
            name="ds", base_url="https://x",
            api_key="sk-abcdefgh1234", model="m",
        )
    )
    from explain_engine.chat.slash_commands import _handle_llm

    events = await _handle_llm(None, ["show"])
    content = events[0].content
    assert "ds" in content and "★active" in content
    # api_key 脱敏 (不出现完整 key)
    assert "sk-abcdefgh1234" not in content
    assert "sk-a" in content  # 前缀


@pytest.mark.asyncio
async def test_slash_llm_use_switches_active(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    store = LLMConfigStore()
    store.upsert(LLMProfile(name="a", base_url="https://x", api_key="k", model="m"))
    store.upsert(LLMProfile(name="b", base_url="https://y", api_key="k2", model="m2"))
    from explain_engine.chat.slash_commands import _handle_llm

    events = await _handle_llm(None, ["use", "b"])
    assert events[0].type == "slash_llm_reload"
    assert LLMConfigStore().load().active == "b"


@pytest.mark.asyncio
async def test_slash_llm_use_unknown_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    LLMConfigStore().upsert(
        LLMProfile(name="a", base_url="https://x", api_key="k", model="m")
    )
    from explain_engine.chat.slash_commands import _handle_llm

    events = await _handle_llm(None, ["use", "nope"])
    assert events[0].type == "slash_error"


@pytest.mark.asyncio
async def test_slash_llm_no_args_opens_modal(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    from explain_engine.chat.slash_commands import _handle_llm

    events = await _handle_llm(None, [])
    assert events[0].type == "slash_open_llm_config"
