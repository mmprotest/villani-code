from cache.memory_cache import MemoryCache
from cache.serializer import serialize


def test_serializer_returns_bytes_for_hashing_pipeline() -> None:
    payload = serialize([1, 2, 3])
    assert isinstance(payload, bytes)


def test_cache_key_generation_accepts_list_values() -> None:
    cache = MemoryCache()
    key = cache.put(["a", "b"])
    assert cache.get(key) == ["a", "b"]
    assert len(key) == 64
