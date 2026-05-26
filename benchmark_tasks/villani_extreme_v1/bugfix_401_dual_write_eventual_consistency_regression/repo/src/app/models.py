from dataclasses import dataclass
@dataclass(frozen=True)
class AdjustmentCommand:
    account_id: str
    amount: int
    request_id: str
    received_at_ms: int
@dataclass(frozen=True)
class AdjustmentApplied:
    account_id: str
    amount: int
    request_id: str
    dedupe_key: str
