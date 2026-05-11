from explain_agent.core.types import DataAdapter, AdapterQuery, Evidence


class AdapterError(RuntimeError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, DataAdapter] = {}

    def register(self, adapter: DataAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> DataAdapter:
        if name not in self._adapters:
            raise AdapterError(f"adapter not registered: {name}")
        return self._adapters[name]

    def list_names(self) -> list[str]:
        return sorted(self._adapters.keys())
