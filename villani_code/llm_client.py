from __future__ import annotations

from typing import Any, Generator, Protocol


class LLMClient(Protocol):
    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any] | Generator[dict[str, Any], None, None]:
        ...

