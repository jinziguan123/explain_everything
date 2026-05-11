from pathlib import Path

import yaml


class FrameworkError(RuntimeError):
    pass


_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "frameworks"


def load_framework(domain_id: str, search_paths: list[Path] | None = None) -> dict:
    paths = search_paths or [_DEFAULT_DIR]
    for p in paths:
        candidate = p / f"{domain_id}.yaml"
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                fw = yaml.safe_load(f)
            _validate(fw)
            return fw
    raise FrameworkError(f"framework not found: {domain_id}")


def _validate(fw: dict) -> None:
    for key in ("domain_id", "dimensions", "worker_config", "models"):
        if key not in fw:
            raise FrameworkError(f"missing key in framework: {key}")
    for dim in fw["dimensions"]:
        for k in ("id", "name", "priority", "data_sources", "query_template"):
            if k not in dim:
                raise FrameworkError(f"dimension {dim} missing key: {k}")
