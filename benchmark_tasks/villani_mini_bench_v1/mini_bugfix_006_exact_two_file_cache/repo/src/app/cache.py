from __future__ import annotations
from .serializer import freeze_value

class Cache:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], object] = {}

    def put(self, namespace: str, key: object, value: object) -> None:
        self._store[(namespace, str(key))] = value

    def get(self, namespace: str, key: object) -> object | None:
        return self._store.get((namespace, str(key)))
