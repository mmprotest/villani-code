from dataclasses import dataclass

@dataclass
class Event:
    kind: str
    amount: int
    source: str | None = None
