import hashlib

from .serializer import serialize


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def _key(self, value: object) -> str:
        payload = serialize(value)
        return hashlib.sha256(payload).hexdigest()

    def put(self, value: object) -> str:
        key = self._key(value)
        self._data[key] = value
        return key

    def get(self, key: str) -> object | None:
        return self._data.get(key)
