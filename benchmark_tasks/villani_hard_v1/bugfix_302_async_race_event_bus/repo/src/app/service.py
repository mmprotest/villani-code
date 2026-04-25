from concurrent.futures import ThreadPoolExecutor
from .stats import EventStats

def record_many(event_name: str, n: int, workers: int = 8) -> int:
    stats = EventStats()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda _: stats.record(event_name), range(n)))
    return stats.count_for(event_name)
