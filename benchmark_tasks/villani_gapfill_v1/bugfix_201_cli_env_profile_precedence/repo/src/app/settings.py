from dataclasses import dataclass

@dataclass
class Settings:
    mode: str = "safe"
    retries: int = 1
