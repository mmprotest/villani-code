from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PROTOCOL_VERSION = 1
BridgeMode = Literal["runner", "villani"]


@dataclass(slots=True)
class BridgeConfig:
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


@dataclass(slots=True)
class BridgeLimits:
    max_turns: int | None = None


@dataclass(slots=True)
class RunCommand:
    id: str
    task: str
    repo: str
    mode: BridgeMode = "runner"
    config: BridgeConfig = field(default_factory=BridgeConfig)
    limits: BridgeLimits = field(default_factory=BridgeLimits)


@dataclass(slots=True)
class PingCommand:
    id: str


@dataclass(slots=True)
class AbortCommand:
    id: str


def to_json_line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"


def parse_json_line(line: str) -> dict[str, Any]:
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError("Bridge command must be a JSON object")
    return payload


def _optional_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_run_command(payload: dict[str, Any]) -> RunCommand:
    run_id = str(payload.get("id") or "").strip()
    task = str(payload.get("task") or "").strip()
    repo = str(payload.get("repo") or "").strip()
    mode = str(payload.get("mode") or "runner").strip().lower()
    if not run_id:
        raise ValueError("run command requires id")
    if not task:
        raise ValueError("run command requires task")
    if not repo:
        raise ValueError("run command requires repo")
    if mode not in {"runner", "villani"}:
        raise ValueError("run mode must be 'runner' or 'villani'")
    config_payload = _optional_dict(payload.get("config"))
    limits_payload = _optional_dict(payload.get("limits"))
    max_turns = limits_payload.get("max_turns")
    return RunCommand(
        id=run_id,
        task=task,
        repo=repo,
        mode=mode,  # type: ignore[arg-type]
        config=BridgeConfig(
            provider=str(config_payload["provider"]) if config_payload.get("provider") else None,
            model=str(config_payload["model"]) if config_payload.get("model") else None,
            base_url=str(config_payload["base_url"]) if config_payload.get("base_url") else None,
            api_key=str(config_payload["api_key"]) if config_payload.get("api_key") else None,
        ),
        limits=BridgeLimits(max_turns=int(max_turns) if max_turns is not None else None),
    )


def ready_event() -> dict[str, Any]:
    return {"type": "ready", "protocol_version": PROTOCOL_VERSION}


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
