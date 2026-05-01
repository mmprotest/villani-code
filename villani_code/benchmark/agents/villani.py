from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Iterable

from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
from villani_code.benchmark.agents.base import AgentRunner
from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality


class VillaniAgentRunner(AgentRunner):
    name = "villani"
    capability = "native_runtime_instrumented"
    telemetry_capability = "structured_runtime_events"
    fairness_classification = FairnessClassification.APPROXIMATELY_COMPARABLE
    fairness_notes = "Shared benchmark contract and harness-only scoring improve comparability, but telemetry richness still differs across adapters."
    command_capture = FieldQuality.EXACT
    file_event_capture = FieldQuality.EXACT
    verify_capture = FieldQuality.EXACT

    def build_command(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        benchmark_config_json: str | None = None,
    ) -> list[str]:
        if not model:
            raise ValueError("villani requires --model")
        command = [
            sys.executable,
            "-m",
            "villani_code.cli",
            "run",
            prompt,
            "--repo",
            str(repo_path),
            "--provider",
            provider or ("openai" if base_url else "anthropic"),
            "--model",
            model,
            "--no-stream",
        ]
        if base_url:
            command.extend(["--base-url", base_url])
        if api_key:
            command.extend(["--api-key", api_key])
        if benchmark_config_json:
            command.extend(["--benchmark-runtime-json", benchmark_config_json])
        return command

    def run_agent(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        timeout: int,
        benchmark_config_json: str | None = None,
        debug_dir: Path | None = None,
    ) -> AdapterRunResult:
        base = super().run_agent(
            repo_path,
            prompt,
            model,
            base_url,
            api_key,
            provider,
            timeout,
            benchmark_config_json=benchmark_config_json,
            debug_dir=debug_dir,
        )
        events: list[AdapterEvent] = []
        candidates: list[Path] = []
        candidates.extend(sorted((repo_path / ".villani_code" / "missions").glob("*/runtime_events.jsonl")))
        candidates.extend(sorted((repo_path / ".villani_code" / "missions").glob("*/tool_calls.jsonl")))
        candidates.extend(sorted((repo_path / "villani_debug").glob("*/events.jsonl")))
        candidates.extend(sorted((repo_path / "villani_debug").glob("*/tool_calls.jsonl")))
        legacy = repo_path / ".villani_code" / "runtime_events.jsonl"
        if legacy.exists():
            candidates.append(legacy)
        if candidates:
            latest_mtime = max(p.stat().st_mtime for p in candidates if p.exists())
            window = [p for p in candidates if p.exists() and p.stat().st_mtime >= latest_mtime - 120]
            for path in window:
                for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not raw.strip():
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    runtime_type = str(payload.get("type") or payload.get("event") or "runtime_event").strip() or "runtime_event"
                    ts = float(payload.get("ts") or payload.get("timestamp") or time.time())
                    events.append(AdapterEvent(type=runtime_type, timestamp=ts, payload=payload))
        return AdapterRunResult(
            **base.model_dump(exclude={"events", "telemetry_quality", "telemetry_field_quality_map"}),
            events=base.events + events,
            telemetry_quality=TelemetryQuality.EXACT if events else TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
        )
