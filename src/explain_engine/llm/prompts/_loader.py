"""YAML prompt 模板加载器。"""

from pathlib import Path
from typing import Any

import yaml

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> dict[str, Any]:
    """加载 yaml prompt 模板，返回 dict。

    Raises:
        FileNotFoundError: prompt 文件不存在
        yaml.YAMLError: yaml 解析失败
    """
    p = _PROMPTS_DIR / f"{name}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"prompt {name} not found at {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))
