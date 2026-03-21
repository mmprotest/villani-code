from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from villani_code.benchmark.failure_taxonomy import classify_failure_taxonomy
from villani_code.benchmark.models import (
    BENCHMARK_VERSION,
    BenchmarkRunResult,
    BenchmarkTask,
    BenchmarkTrack,
    FailureReason,
    FairnessClassification,
    ReproducibilityManifest,
    TaskDifficulty,
    TaskFamily,
    TaskSource,
    TelemetryQuality,
)
from villani_code.benchmark.reporting import write_results
from villani_code.benchmark.task_loader import load_tasks


@dataclass(frozen=True)
class ResultKey:
    task_id: str
    repeat_index: int
    agent_name: str


@dataclass
class RebuildSummary:
    output_dir: Path
    rebuilt_count: int
    skipped_count: int
    exact_count: int
    partial_count: int
    source_files: list[str] = field(default_factory=list)
    missing_common_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Collected:
    result: BenchmarkRunResult
    quality: str
    source: str


def rebuild_results_from_directory(output_dir: Path, *, task_suite_roots: list[Path] | None = None) -> RebuildSummary:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_files: set[str] = set()
    warnings: list[str] = []

    recovered: dict[ResultKey, _Collected] = {}

    full_results = _discover_full_result_jsons(output_dir)
    for path, row in full_results:
        key = ResultKey(task_id=row.task_id, repeat_index=row.repeat_index, agent_name=row.agent_name)
        recovered[key] = _Collected(result=row, quality="exact", source=str(path))
        source_files.add(str(path))

    for path, row in _load_partial_results_jsonl(output_dir / "results.jsonl"):
        key = ResultKey(task_id=row.task_id, repeat_index=row.repeat_index, agent_name=row.agent_name)
        if key not in recovered:
            recovered[key] = _Collected(result=row, quality="exact", source=str(path))
            source_files.add(str(path))

    task_index = _load_task_index(task_suite_roots)
    verify_meta = _load_verifier_metadata(output_dir)
    for manifest_path, manifest in _load_manifests(output_dir):
        key = ResultKey(task_id=manifest.task_id, repeat_index=manifest.repeat_index, agent_name=manifest.agent_name)
        if key in recovered:
            continue
        rebuilt = _rebuild_from_manifest(
            manifest,
            task_index.get(manifest.task_id),
            verify_meta.get((manifest.task_id, manifest.repeat_index)),
            manifest_path=manifest_path,
        )
        if rebuilt is None:
            warnings.append(f"manifest {manifest_path.name} could not be reconstructed (missing task schema)")
            continue
        recovered[key] = _Collected(result=rebuilt, quality="partial", source=str(manifest_path))
        source_files.add(str(manifest_path))

    for key, row, csv_path in _load_results_csv_fallback(output_dir / "results.csv"):
        if key not in recovered:
            recovered[key] = _Collected(result=row, quality="partial", source=str(csv_path))
            source_files.add(str(csv_path))

    rebuilt_rows = [col.result for _, col in sorted(recovered.items(), key=lambda item: (item[0].task_id, item[0].repeat_index, item[0].agent_name))]
    if rebuilt_rows:
        write_results(rebuilt_rows, output_dir)

    exact_count = sum(1 for c in recovered.values() if c.quality == "exact")
    partial_count = sum(1 for c in recovered.values() if c.quality != "exact")
    missing_common_fields = _detect_common_missing_fields(rebuilt_rows)

    meta_payload = {
        "source_directory": str(output_dir),
        "rebuilt_at": datetime.now(UTC).isoformat(),
        "results_rebuilt": len(rebuilt_rows),
        "source_inputs": sorted(source_files),
        "reconstruction_quality": "exact" if partial_count == 0 else "partial",
        "exact_results": exact_count,
        "partial_results": partial_count,
        "commonly_unavailable_fields": missing_common_fields,
        "warnings": warnings,
    }
    (output_dir / "rebuild_meta.json").write_text(json.dumps(meta_payload, indent=2) + "\n", encoding="utf-8")

    return RebuildSummary(
        output_dir=output_dir,
        rebuilt_count=len(rebuilt_rows),
        skipped_count=0,
        exact_count=exact_count,
        partial_count=partial_count,
        source_files=sorted(source_files),
        missing_common_fields=missing_common_fields,
        warnings=warnings,
    )


def _discover_full_result_jsons(output_dir: Path) -> list[tuple[Path, BenchmarkRunResult]]:
    discovered: list[tuple[Path, BenchmarkRunResult]] = []
    for path in output_dir.rglob("*.json"):
        if path.name in {"summary.json", "aggregates.json", "rebuild_meta.json"}:
            continue
        if path.name.startswith("manifest_"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        if "task_id" not in payload or "agent_name" not in payload:
            continue
        try:
            discovered.append((path, BenchmarkRunResult.model_validate(payload)))
        except ValidationError:
            continue
    return discovered


def _load_partial_results_jsonl(path: Path) -> list[tuple[Path, BenchmarkRunResult]]:
    rows: list[tuple[Path, BenchmarkRunResult]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append((path, BenchmarkRunResult.model_validate_json(line)))
        except ValidationError:
            continue
    return rows


def _load_manifests(output_dir: Path) -> list[tuple[Path, ReproducibilityManifest]]:
    manifests: list[tuple[Path, ReproducibilityManifest]] = []
    for path in sorted(output_dir.glob("manifest_*.json")):
        try:
            manifests.append((path, ReproducibilityManifest.model_validate_json(path.read_text(encoding="utf-8"))))
        except (ValidationError, OSError):
            continue
    return manifests


def _load_task_index(task_suite_roots: list[Path] | None) -> dict[str, object]:
    roots = task_suite_roots or [Path("benchmark_tasks/villani_bench_v1")]
    index: dict[str, object] = {}
    for root in roots:
        if not root.exists():
            continue
        try:
            tasks = load_tasks(root)
        except Exception:
            continue
        for task in tasks:
            index[task.id] = task
    return index


def _load_verifier_metadata(output_dir: Path) -> dict[tuple[str, int], dict[str, object]]:
    payload: dict[tuple[str, int], dict[str, object]] = {}
    debug_dir = output_dir / "agent_debug"
    if not debug_dir.exists():
        return payload
    for meta_path in debug_dir.rglob("*_verify_*_meta.json"):
        run_dir = meta_path.parent.name
        if "__r" not in run_dir:
            continue
        task_id, _, rep = run_dir.partition("__r")
        try:
            repeat = int(rep)
        except ValueError:
            continue
        key = (task_id, repeat)
        bucket = payload.setdefault(key, {"visible": [], "hidden": []})
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stage = str(meta.get("stage", "")).lower()
        if "visible" in stage:
            bucket["visible"].append(meta)
        elif "hidden" in stage:
            bucket["hidden"].append(meta)
    return payload


def _all_pass(meta_rows: list[dict[str, object]]) -> bool:
    return bool(meta_rows) and all(bool(row.get("passed")) for row in meta_rows)


def _rebuild_from_manifest(
    manifest: ReproducibilityManifest,
    task: BenchmarkTask | None,
    verify_meta: dict[str, object] | None,
    *,
    manifest_path: Path,
) -> BenchmarkRunResult | None:
    if task is None:
        return None
    visible_rows = list((verify_meta or {}).get("visible", []))
    hidden_rows = list((verify_meta or {}).get("hidden", []))
    visible_pass = _all_pass(visible_rows)
    hidden_pass = _all_pass(hidden_rows)
    timeout = False

    if visible_rows:
        timeout = any(row.get("exit_code") is None for row in visible_rows)
    elif hidden_rows:
        timeout = any(row.get("exit_code") is None for row in hidden_rows)

    success = int(visible_pass and hidden_pass and not timeout)
    failure_reason = None
    if not success:
        if timeout:
            failure_reason = FailureReason.TIMEOUT
        elif visible_rows and not visible_pass:
            failure_reason = FailureReason.VISIBLE_VERIFICATION_FAILED
        elif hidden_rows and visible_pass and not hidden_pass:
            failure_reason = FailureReason.HIDDEN_VERIFICATION_FAILED
    verification_output = "\n".join(
        str(part)
        for row in [*visible_rows, *hidden_rows]
        for part in (row.get("stdout"), row.get("stderr"))
        if part
    )
    failure_taxonomy, failure_taxonomy_detail = classify_failure_taxonomy(
        success=success,
        failure_reason=failure_reason,
        visible_pass=visible_pass,
        hidden_pass=hidden_pass,
        verification_output=verification_output,
        files_touched=0,
        meaningful_touched_paths=[],
        meaningful_expected_paths=[],
        meaningful_unexpected_paths=[],
        touched_file_paths=[],
        expected_files=task.metadata.expected_files,
        task_family=task.family,
        task_type=task.task_type or task.metadata.task_type,
        benchmark_category=task.benchmark_category or task.metadata.benchmark_category,
    )

    return BenchmarkRunResult(
        benchmark_version=BENCHMARK_VERSION,
        benchmark_track=task.benchmark_track,
        task_id=manifest.task_id,
        task_version=manifest.task_version,
        benchmark_category=task.benchmark_category or task.metadata.benchmark_category,
        task_family=task.family,
        task_difficulty=task.difficulty,
        task_language=task.language,
        task_source_type=task.source_type,
        task_tags=task.tags,
        task_type=task.task_type or task.metadata.task_type,
        benchmark_bucket=task.metadata.benchmark_bucket,
        runtime_stressors=task.metadata.runtime_stressors,
        expected_files=task.metadata.expected_files,
        task_checksum=manifest.task_checksum,
        agent_name=manifest.agent_name,
        adapter_name=manifest.adapter_name,
        adapter_version=manifest.adapter_version,
        adapter_capability="unknown_reconstructed",
        fairness_classification=FairnessClassification.NOT_COMPARABLE,
        fairness_notes="reconstructed from manifest artifacts",
        telemetry_capability="reconstructed",
        model_name=manifest.model_name,
        provider_label=manifest.provider,
        success=success,
        pass_rate=float(success),
        failed=1 - success,
        timed_out=int(timeout),
        visible_pass=visible_pass,
        hidden_pass=hidden_pass,
        visible_only_pass=visible_pass and not hidden_pass,
        runtime_seconds=0.0,
        wall_clock_seconds=None,
        timeout=timeout,
        failure_reason=failure_reason,
        failure_taxonomy=failure_taxonomy,
        failure_taxonomy_detail=failure_taxonomy_detail,
        forbidden_reason_detail=None,
        policy_warning="reconstructed_result",
        policy_warning_detail="recovered from manifest + verifier metadata",
        error=None,
        agent_exit_code=None,
        stderr_preview=None,
        touched_file_paths=[],
        raw_touched_file_paths=[],
        normalized_touched_paths=[],
        runtime_artifact_paths=[],
        path_classifications={},
        meaningful_touched_paths=[],
        meaningful_expected_paths=[],
        meaningful_unexpected_paths=[],
        files_touched=0,
        lines_added=0,
        lines_deleted=0,
        num_shell_commands=None,
        num_failed_commands=None,
        tokens_input=None,
        tokens_output=None,
        total_tokens=None,
        estimated_cost=None,
        number_of_turns=None,
        tool_calls_total=None,
        file_reads=None,
        file_writes=None,
        patch_attempts=None,
        test_runs=None,
        retries_after_failure=None,
        first_pass_success=None,
        recovered_after_failed_attempt=None,
        expected_files_touched_count=None,
        actual_files_touched_count=0,
        touched_unexpected_files=None,
        unrelated_file_touch=False,
        verification_relevant=False,
        recovery_attempted=False,
        recovery_success=None,
        no_progress_termination=False,
        verifications_run=[str(row.get("command")) for row in [*visible_rows, *hidden_rows] if row.get("command")],
        verification_attempt_count=len(visible_rows) + len(hidden_rows),
        time_to_first_edit=None,
        time_to_first_verify=None,
        last_verification_time=None,
        expected_files_found=None,
        expected_files_total=(len(task.metadata.expected_files) if task.metadata.expected_files else None),
        expected_file_first_read_time=None,
        self_corrected_after_failed_verify=None,
        touched_irrelevant_files=None,
        telemetry_quality=TelemetryQuality.UNAVAILABLE,
        telemetry_field_quality_map={},
        workspace_preserved=manifest.workspace_preserved,
        reproducibility_manifest_path=str(manifest_path),
        prompt_artifact_path=None,
        contract_artifact_path=None,
        scoring_inputs_mode="harness_only",
        repeat_index=manifest.repeat_index,
    )


def _load_results_csv_fallback(path: Path) -> list[tuple[ResultKey, BenchmarkRunResult, Path]]:
    recovered: list[tuple[ResultKey, BenchmarkRunResult, Path]] = []
    if not path.exists():
        return recovered
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                result = BenchmarkRunResult(
                    benchmark_version=BENCHMARK_VERSION,
                    benchmark_track=BenchmarkTrack(str(row.get("benchmark_track") or "core")),
                    task_id=str(row.get("task_id") or ""),
                    task_version="1.0",
                    task_family=TaskFamily.BUGFIX,
                    task_difficulty=TaskDifficulty.MEDIUM,
                    task_language="unknown",
                    task_source_type=TaskSource.CURATED,
                    task_tags=[],
                    task_type=row.get("task_type") or None,
                    benchmark_bucket=str(row.get("benchmark_bucket") or "baseline"),
                    runtime_stressors=[],
                    expected_files=[],
                    task_checksum="",
                    agent_name=str(row.get("agent_name") or "unknown"),
                    adapter_name=str(row.get("adapter_name") or "unknown"),
                    adapter_version="reconstructed",
                    adapter_capability=str(row.get("adapter_capability") or "unknown_reconstructed"),
                    fairness_classification=FairnessClassification.NOT_COMPARABLE,
                    fairness_notes="reconstructed from csv fallback",
                    telemetry_capability=str(row.get("telemetry_capability") or "reconstructed"),
                    model_name=row.get("model_name") or None,
                    provider_label=None,
                    success=int(float(row.get("success") or 0)),
                    pass_rate=float(row.get("pass_rate") or 0),
                    failed=int(float(row.get("failed") or 0)),
                    timed_out=int(float(row.get("timed_out") or 0)),
                    visible_pass=False,
                    hidden_pass=False,
                    runtime_seconds=float(row.get("runtime_seconds") or 0),
                    wall_clock_seconds=float(row["wall_clock_seconds"]) if row.get("wall_clock_seconds") else None,
                    timeout=bool(int(float(row.get("timed_out") or 0))),
                    touched_file_paths=[],
                    files_touched=int(float(row.get("files_touched") or 0)),
                    lines_added=0,
                    lines_deleted=0,
                    telemetry_quality=TelemetryQuality.UNAVAILABLE,
                    verifications_run=[],
                    reproducibility_manifest_path=None,
                    repeat_index=0,
                )
            except Exception:
                continue
            key = ResultKey(task_id=result.task_id, repeat_index=result.repeat_index, agent_name=result.agent_name)
            recovered.append((key, result, path))
    return recovered


def _detect_common_missing_fields(rows: list[BenchmarkRunResult]) -> list[str]:
    if not rows:
        return []
    checks = {
        "runtime_seconds_zero": all(r.runtime_seconds == 0 for r in rows),
        "touched_file_paths_missing": all(not r.touched_file_paths for r in rows),
        "total_tokens_missing": all(r.total_tokens is None for r in rows),
        "agent_exit_code_missing": all(r.agent_exit_code is None for r in rows),
    }
    return [name for name, missing in checks.items() if missing]
