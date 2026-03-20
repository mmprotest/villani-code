from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

_PROMPT_KEYS = ("prompt_tokens", "input_tokens", "tokens_input")
_COMPLETION_KEYS = ("completion_tokens", "output_tokens", "tokens_output")
_TOTAL_KEYS = ("total_tokens",)
_CACHED_KEYS = ("cached_tokens", "cache_read_input_tokens", "cache_read_tokens")
_REASONING_KEYS = ("reasoning_tokens",)
_USAGE_MARKER_KEYS = {
    *_PROMPT_KEYS,
    *_COMPLETION_KEYS,
    *_TOTAL_KEYS,
    *_CACHED_KEYS,
    *_REASONING_KEYS,
    "usage",
    "input_token_details",
    "output_token_details",
    "completion_tokens_details",
}


class TokenUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None

    def has_any(self) -> bool:
        return any(value is not None for value in self.model_dump().values())

    def add(self, other: "TokenUsage") -> "TokenUsage":
        def merged(current: int | None, incoming: int | None) -> int | None:
            if current is None:
                return incoming
            if incoming is None:
                return current
            return current + incoming

        return TokenUsage(
            prompt_tokens=merged(self.prompt_tokens, other.prompt_tokens),
            completion_tokens=merged(self.completion_tokens, other.completion_tokens),
            total_tokens=merged(self.total_tokens, other.total_tokens),
            cached_tokens=merged(self.cached_tokens, other.cached_tokens),
            reasoning_tokens=merged(self.reasoning_tokens, other.reasoning_tokens),
        )


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _pick_first(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _coerce_int(payload.get(key))
        if value is not None:
            return value
    return None


def normalize_usage_payload(payload: dict[str, Any]) -> TokenUsage:
    prompt_tokens = _pick_first(payload, _PROMPT_KEYS)
    completion_tokens = _pick_first(payload, _COMPLETION_KEYS)
    total_tokens = _pick_first(payload, _TOTAL_KEYS)
    cached_tokens = _pick_first(payload, _CACHED_KEYS)
    reasoning_tokens = _pick_first(payload, _REASONING_KEYS)

    input_details = payload.get("input_token_details")
    if isinstance(input_details, dict):
        cached_tokens = cached_tokens if cached_tokens is not None else _coerce_int(input_details.get("cached_tokens"))

    output_details = payload.get("output_token_details")
    if isinstance(output_details, dict):
        reasoning_tokens = reasoning_tokens if reasoning_tokens is not None else _coerce_int(output_details.get("reasoning_tokens"))

    completion_details = payload.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        reasoning_tokens = reasoning_tokens if reasoning_tokens is not None else _coerce_int(completion_details.get("reasoning_tokens"))

    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _iter_usage_candidates(obj: Any) -> Iterable[TokenUsage]:
    if isinstance(obj, str):
        stripped = obj.strip()
        if not stripped or stripped[0] not in "[{":
            return
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return
        yield from _iter_usage_candidates(parsed)
        return

    if isinstance(obj, list):
        for item in obj:
            yield from _iter_usage_candidates(item)
        return

    if not isinstance(obj, dict):
        return

    usage_payload = obj.get("usage")
    if isinstance(usage_payload, dict):
        normalized = normalize_usage_payload(usage_payload)
        if normalized.has_any():
            yield normalized

    has_markers = any(key in obj for key in _USAGE_MARKER_KEYS)
    if has_markers:
        normalized = normalize_usage_payload(obj)
        if normalized.has_any():
            yield normalized
        skip_keys = {"usage", "input_token_details", "output_token_details", "completion_tokens_details"}
    else:
        skip_keys = set()

    for key, value in obj.items():
        if key in skip_keys:
            continue
        yield from _iter_usage_candidates(value)


def _iter_text_json_objects(text: str) -> Iterable[Any]:
    stripped = text.strip()
    if not stripped:
        return
    if stripped[0] in "[{":
        try:
            yield json.loads(stripped)
        except json.JSONDecodeError:
            pass
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] not in "[{":
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def extract_token_usage(*, stdout: str = "", stderr: str = "", events: list[object] | None = None) -> TokenUsage:
    total = TokenUsage()
    for event in events or []:
        payload = getattr(event, "payload", None)
        if payload is None and isinstance(event, dict):
            payload = event.get("payload", event)
        if isinstance(payload, dict):
            for candidate in _iter_usage_candidates(payload):
                total = total.add(candidate)
    for stream_text in (stdout, stderr):
        for candidate_obj in _iter_text_json_objects(stream_text):
            for candidate in _iter_usage_candidates(candidate_obj):
                total = total.add(candidate)
    return total
