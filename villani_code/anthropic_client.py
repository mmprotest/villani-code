from __future__ import annotations

from typing import Any, Generator

import httpx

from villani_code.streaming import parse_sse_events


class AnthropicClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any] | Generator[dict[str, Any], None, None]:
        url = f"{self.base_url}/v1/messages"
        if not stream:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                return response.json()

        def gen() -> Generator[dict[str, Any], None, None]:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    for event in parse_sse_events(response.iter_lines()):
                        yield event

        return gen()
