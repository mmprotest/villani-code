from __future__ import annotations

import json
import sys
import time
from pathlib import Path

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

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    @classmethod
    def _usage_from_payload(cls, payload: dict[str, object]) -> tuple[int | None, int | None, int | None]:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None, None, None
        tokens_in = next(
            (
                value
                for value in (
                    cls._coerce_int(usage.get("input_tokens")),
                    cls._coerce_int(usage.get("prompt_tokens")),
                    cls._coerce_int(usage.get("prompt_token_count")),
                )
                if value is not None
            ),
            None,
        )
        tokens_out = next(
            (
                value
                for value in (
                    cls._coerce_int(usage.get("output_tokens")),
                    cls._coerce_int(usage.get("completion_tokens")),
                    cls._coerce_int(usage.get("completion_token_count")),
                )
                if value is not None
            ),
            None,
        )
        total = next(
            (
                value
                for value in (
                    cls._coerce_int(usage.get("total_tokens")),
                    cls._coerce_int(payload.get("total_tokens")),
                )
                if value is not None
            ),
            None,
        )
        if total is None and tokens_in is not None and tokens_out is not None:
            total = tokens_in + tokens_out
        return tokens_in, tokens_out, total

    @classmethod
    def _extract_usage_from_latest_transcript(cls, repo_path: Path) -> tuple[int | None, int | None, int | None]:
        transcript_dir = repo_path / ".villani_code" / "transcripts"
        if not transcript_dir.exists():
            return None, None, None
        transcripts = sorted(transcript_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not transcripts:
            return None, None, None
        try:
            payload = json.loads(transcripts[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, None, None
        responses = payload.get("responses")
        if not isinstance(responses, list):
            return None, None, None
        input_total = 0
        output_total = 0
        total_total = 0
        saw_input = False
        saw_output = False
        saw_total = False
        for response in responses:
            if not isinstance(response, dict):
                continue
            tokens_in, tokens_out, total = cls._usage_from_payload(response)
            if tokens_in is not None:
                input_total += tokens_in
                saw_input = True
            if tokens_out is not None:
                output_total += tokens_out
                saw_output = True
            if total is not None:
                total_total += total
                saw_total = True
        final_in = input_total if saw_input else None
        final_out = output_total if saw_output else None
        final_total = total_total if saw_total else (final_in + final_out if final_in is not None and final_out is not None else None)
        return final_in, final_out, final_total

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
        events_file = repo_path / ".villani_code" / "runtime_events.jsonl"
        events: list[AdapterEvent] = []
        if events_file.exists():
            for raw in events_file.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                runtime_type = str(payload.get("type") or "").strip()
                if not runtime_type:
                    runtime_type = str(payload.get("event") or "").strip()
                if not runtime_type:
                    runtime_type = "runtime_event"
                events.append(AdapterEvent(type=runtime_type, timestamp=float(payload.get("ts", time.time())), payload=payload))
        tokens_input, tokens_output, total_tokens = self._extract_usage_from_latest_transcript(repo_path)
        return AdapterRunResult(
            **base.model_dump(
                exclude={
                    "events",
                    "telemetry_quality",
                    "telemetry_field_quality_map",
                    "tokens_input",
                    "tokens_output",
                    "total_tokens",
                }
            ),
            tokens_input=tokens_input if tokens_input is not None else base.tokens_input,
            tokens_output=tokens_output if tokens_output is not None else base.tokens_output,
            total_tokens=total_tokens if total_tokens is not None else base.total_tokens,
            events=base.events + events,
            telemetry_quality=TelemetryQuality.EXACT if events else TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
        )
