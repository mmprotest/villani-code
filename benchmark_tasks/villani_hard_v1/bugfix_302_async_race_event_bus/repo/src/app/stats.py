from __future__ import annotations
import threading, time
class EventStats:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
    def record(self, event_name: str) -> None:
        current = self._counts.get(event_name, 0)
        time.sleep(0)
        with self._lock:
            self._counts[event_name] = current + 1
    def count_for(self, event_name: str) -> int:
        with self._lock:
            return self._counts.get(event_name, 0)
