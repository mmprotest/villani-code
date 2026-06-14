from __future__ import annotations

from typing import Any, Generator

import httpx

from villani_code.streaming import parse_sse_events


class AnthropicClient:
    def __init__(self, base_url: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create_message(self, payload: dict[str, Any], stream: bool) -> dict[str, Any] | Generator[dict[str, Any], None, None]:
        url = f"{self.base_url}/v1/messages"
        if not stream:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    response_text = exc.response.text
                    raise RuntimeError(
                        f"Anthropic-compatible /v1/messages request failed: {exc}. Response body: {response_text}"
                    ) from exc
                return response.json()

        def gen() -> Generator[dict[str, Any], None, None]:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", url, json=payload) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        response_text = exc.response.text
                        raise RuntimeError(
                            f"Anthropic-compatible /v1/messages stream request failed: {exc}. Response body: {response_text}"
                        ) from exc
                    for event in parse_sse_events(response.iter_lines()):
                        yield event

        return gen()
