from dataclasses import dataclass
@dataclass(frozen=True)
class Event:
    seq:int
    kind:str
    account:str
    amount:int
