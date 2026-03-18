from __future__ import annotations

class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, str] = {}
        self._aliases: dict[str, str] = {}

    def register(self, name: str, handler: str, alias: str | None = None) -> None:
        self._commands[name] = handler
        if alias:
            self._aliases[alias] = alias

    def resolve(self, name: str) -> str:
        target = self._aliases.get(name, name)
        return self._commands[target]
