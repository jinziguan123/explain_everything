"""Phase 20.4: 用户可管理的 LLM provider 配置 (多 profile + 热切换)。

之前 LLM 配置纯 env 驱动 (.env / os.environ, make_llm_client 启动读一次)。
本模块加一层用户可在 app 内直接管理的配置: 多套命名 profile (各含 protocol /
base_url / api_key / model + 可选 light 覆盖), 持久化到 EXPLAIN_HOME/llm_config.json,
可随时切换 active。

优先级 (config.py make_*_llm_client):
- llm_config.json 有 active profile → 用 profile 构造 client。
- 无 (文件缺 / active=None / 解析失败) → fallback 现有 .env / env 路径 (向后兼容)。

适配 anthropic + openai 两协议: profile.protocol 决定路由, 跟现有 LLM_PROTOCOL
语义一致。light LLM: profile 的 light_* 字段缺省时 fallback 该 profile 主字段
(跟现有 LLM_LIGHT_* fallback LLM_* 语义对齐)。

安全: api_key 明文存 JSON (跟 .env 明文一致), save 时 chmod 0600 限本用户读写。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Protocol = Literal["anthropic", "openai"]
StructuredMode = Literal["json_schema", "json_object"]

CONFIG_FILENAME = "llm_config.json"


def _explain_home() -> Path:
    """EXPLAIN_HOME 或 fallback ~/.explain (跟其他 explain 文件一致)。

    实时取 env (不 cache 模块加载时值) — 让测试能 monkeypatch EXPLAIN_HOME。
    """
    home = os.environ.get("EXPLAIN_HOME") or os.path.expanduser("~/.explain")
    return Path(home)


class LLMProfile(BaseModel):
    """单个 LLM provider 配置档。

    主字段 (必填): protocol / base_url / api_key / model。
    可选: max_tokens / structured_output_mode (仅 openai)。
    light 覆盖 (全可选, None → fallback 主字段): light_protocol / light_base_url /
    light_api_key / light_model / light_max_tokens。
    """

    name: str = Field(min_length=1)
    protocol: Protocol = "anthropic"
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)
    max_tokens: int | None = Field(default=None, ge=1)
    structured_output_mode: StructuredMode = "json_schema"

    # light LLM 覆盖 (缺省 fallback 主字段)
    light_protocol: Protocol | None = None
    light_base_url: str | None = None
    light_api_key: str | None = None
    light_model: str | None = None
    light_max_tokens: int | None = Field(default=None, ge=1)

    def main_config(self) -> dict:
        """解析主 LLM 有效配置 dict (protocol/base_url/api_key/model/...)。"""
        return {
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "structured_output_mode": self.structured_output_mode,
        }

    def light_config(self) -> dict:
        """解析 light LLM 有效配置 — light_* 缺省 fallback 主字段。

        跟现有 make_light_llm_client 的 per-field fallback 语义一致:
        light_<x> 设了用它, 否则用主 <x>。
        """
        return {
            "protocol": self.light_protocol or self.protocol,
            "base_url": self.light_base_url or self.base_url,
            "api_key": self.light_api_key or self.api_key,
            "model": self.light_model or self.model,
            "max_tokens": (
                self.light_max_tokens
                if self.light_max_tokens is not None
                else self.max_tokens
            ),
            # structured mode 不分主/light (light 一般给 cheap structured task 用,
            # 跟主一致即可) — 复用主字段。
            "structured_output_mode": self.structured_output_mode,
        }


class LLMConfig(BaseModel):
    """整个 llm_config.json 内容: active profile 名 + 所有 profiles。"""

    active: str | None = None
    profiles: dict[str, LLMProfile] = Field(default_factory=dict)

    def active_profile(self) -> LLMProfile | None:
        """当前激活 profile (active 名指向的); active=None 或指向不存在 → None。"""
        if self.active is None:
            return None
        return self.profiles.get(self.active)


class LLMConfigStore:
    """llm_config.json 读写 + profile 管理。

    路径: EXPLAIN_HOME/llm_config.json (全局, 非 per-project — base_url/api_key
    是机器级配置)。文件缺 / 损坏 → load() 返空 LLMConfig (不抛, app 启动不受阻,
    自动 fallback env)。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else _explain_home() / CONFIG_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LLMConfig:
        """读 llm_config.json → LLMConfig。缺文件 / 解析失败 → 空 LLMConfig。"""
        if not self._path.exists():
            return LLMConfig()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return LLMConfig.model_validate(raw)
        except (json.JSONDecodeError, OSError, ValueError):
            # 损坏配置不该阻 app 启动 — 返空 (fallback env), caller 可另行提示。
            return LLMConfig()

    def save(self, config: LLMConfig) -> None:
        """写 llm_config.json (mkdir -p + chmod 0600 防泄 api_key)。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            config.model_dump(), ensure_ascii=False, indent=2,
        )
        self._path.write_text(text, encoding="utf-8")
        try:
            self._path.chmod(0o600)
        except OSError:
            # chmod 失败 (e.g. 某些 FS 不支持) 不致命 — 配置仍写成功。
            pass

    # ── 高层管理操作 (load → mutate → save) ──

    def active_profile(self) -> LLMProfile | None:
        """当前激活 profile (供 config.py make_*_llm_client 用)。"""
        return self.load().active_profile()

    def upsert(self, profile: LLMProfile, *, activate: bool = False) -> LLMConfig:
        """新增 / 覆盖 profile (按 name)。activate=True 时顺便设为 active。

        若这是第一个 profile, 自动设 active (否则 active 仍 None 会 fallback env)。
        """
        cfg = self.load()
        is_first = not cfg.profiles
        cfg.profiles[profile.name] = profile
        if activate or is_first:
            cfg.active = profile.name
        self.save(cfg)
        return cfg

    def delete(self, name: str) -> LLMConfig:
        """删 profile。删的是 active 时, active 切到剩余任一 (无则 None)。"""
        cfg = self.load()
        cfg.profiles.pop(name, None)
        if cfg.active == name:
            cfg.active = next(iter(cfg.profiles), None)
        self.save(cfg)
        return cfg

    def set_active(self, name: str) -> LLMConfig:
        """切换 active profile。name 不存在 → KeyError。"""
        cfg = self.load()
        if name not in cfg.profiles:
            raise KeyError(f"profile {name!r} 不存在")
        cfg.active = name
        self.save(cfg)
        return cfg
