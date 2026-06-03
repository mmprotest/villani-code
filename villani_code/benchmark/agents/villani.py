from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

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
        events_file = self._runtime_events_file_for_current_mission(repo_path)
        events: list[AdapterEvent] = []
        if events_file is not None and events_file.exists():
            for raw in events_file.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                runtime_type = str(payload.get("type") or "").strip()
                if not runtime_type:
                    runtime_type = str(payload.get("event") or "").strip()
                if not runtime_type and isinstance(payload.get("payload"), dict):
                    runtime_type = str(payload["payload"].get("type") or payload["payload"].get("event") or "").strip()
                if not runtime_type:
                    runtime_type = "runtime_event"
                ts_value = payload.get("ts", time.time())
                try:
                    ts_float = float(ts_value)
                except (TypeError, ValueError):
                    ts_float = time.time()
                events.append(AdapterEvent(type=runtime_type, timestamp=ts_float, payload=payload))
        return AdapterRunResult(
            **base.model_dump(exclude={"events", "telemetry_quality", "telemetry_field_quality_map"}),
            events=base.events + events,
            telemetry_quality=TelemetryQuality.EXACT if events else TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
        )

    @staticmethod
    def _runtime_events_file_for_current_mission(repo_path: Path) -> Path | None:
        current = repo_path / ".villani_code" / "missions" / "current.json"
        if current.exists():
            try:
                payload: dict[str, Any] = json.loads(current.read_text(encoding="utf-8"))
                mission_id = str(payload.get("mission_id") or "").strip()
            except Exception:  # noqa: BLE001
                mission_id = ""
            if mission_id:
                return repo_path / ".villani_code" / "missions" / mission_id / "runtime_events.jsonl"
        legacy = repo_path / ".villani_code" / "runtime_events.jsonl"
        return legacy if legacy.exists() else None
