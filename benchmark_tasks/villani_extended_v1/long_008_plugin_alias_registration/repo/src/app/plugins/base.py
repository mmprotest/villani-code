from dataclasses import dataclass

@dataclass
class Plugin:
    name: str
    aliases: list[str]
    handler: str
