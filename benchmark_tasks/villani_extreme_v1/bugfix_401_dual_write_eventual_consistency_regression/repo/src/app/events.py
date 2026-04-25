class EventLog:
    def __init__(self): self._events=[]
    def append(self, event): self._events.append(event)
    def all(self): return list(self._events)
