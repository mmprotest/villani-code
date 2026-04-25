from .models import Event
class EventLog:
    def __init__(self, events): self._events=list(events)
    def read_from(self, seq_exclusive:int): return [e for e in self._events if e.seq>seq_exclusive]
