from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

PRIVATE_WARNING = (
    "Warning: this command modified or depended on runner-private state rather than only "
    "task/workspace state. Success evidence from this context may not reflect final validation."
)
TIMEOUT_MESSAGE = "Command timed out before completion."
NO_PROGRESS_MESSAGE = (
    "No meaningful progress has been detected. Make a materially different change, run a "
    "materially different validation, or submit/stop."
)
TRUNCATION_NOTICE = "[truncated; full details written to debug artifacts]"

MAX_AGENT_STDOUT_CHARS = 6000
MAX_AGENT_STDERR_CHARS = 4000
MAX_AGENT_WARNING_CHARS = 2000
MAX_AGENT_MUTATION_SUMMARY_CHARS = 2000
MAX_AGENT_TOOL_RESULT_CHARS = 12000
MAX_AGENT_WARNING_COUNT = 5
MAX_AGENT_MUTATION_ENTRIES = 10
MAX_AGENT_ATTEMPT_STATE_SUMMARY_CHARS = 1500
MAX_CANDIDATE_SNIPPET_CHARS = 240
MAX_UNRESOLVED_WARNING_CHARS = 1500
MAX_COMPACT_RETRY_MEMORY_CHARS = 2500
MAX_SNAPSHOT_FILES = 5000
MAX_SNAPSHOT_FILE_BYTES = 2_000_000
NO_PROGRESS_MAX_STEPS = 8
NO_PROGRESS_MAX_REPEATED_COMMANDS = 3
NO_PROGRESS_MAX_REPEATED_WARNINGS = 2

DEFAULT_SNAPSHOT_SKIP_DIRS = frozenset(
    {
        ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".venv", "venv", "env", "dist", "build", "target", ".cache",
    }
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:16]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _safe_resolve(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _decode_partial(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _cap_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    room = max(0, limit - len(TRUNCATION_NOTICE) - 1)
    return value[:room] + "\n" + TRUNCATION_NOTICE


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d:[0-5]\d(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]|/)(?:[^\s:\"'(),]+[\\/])*[^\s:\"'(),]*")
_STRUCTURED_FAILURE_PATTERNS = (
    re.compile(r"^(?:[A-Za-z_][\w.]*)(?:Error|Exception):(?:\s|$)"),
    re.compile(r"^(?:AssertionError|ImportError|ModuleNotFoundError|SystemExit|KeyboardInterrupt)(?::|$)"),
    re.compile(r"^(?:FAILED|ERROR)(?:\s|$|:)"),
    re.compile(r"^(?:E\s+)?(?:assert(?:ion)?(?: failed)?|expected\b|actual\b)", re.IGNORECASE),
    re.compile(r"(?:^|[\s:])(?:fatal )?error(?:[A-Z0-9_]*)(?:[\s:]|$)", re.IGNORECASE),
    re.compile(r"(?:cannot find|not found|undefined reference|unresolved external|failed to (?:import|load|compile|build|run))", re.IGNORECASE),
)


def _normalize_failure_text(value: str) -> str:
    value = _ANSI_ESCAPE_RE.sub("", value).strip()
    value = _UUID_RE.sub("<uuid>", value)
    value = _TIMESTAMP_RE.sub("<timestamp>", value)
    value = re.sub(r"\b0x[0-9a-f]+\b", "<address>", value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)(?:/tmp|/var/tmp|/private/tmp)[^\s:\"'(),]*", "<temp-path>", value)
    value = _ABSOLUTE_PATH_RE.sub("<path>", value)
    value = re.sub(r"(?i)\bline\s+\d+\b", "line <n>", value)
    value = re.sub(r"(?<=\w):\d+(?::\d+)?\b", ":<n>", value)
    value = re.sub(r"\bpid[=: ]+\d+\b", "pid=<n>", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def failure_fingerprint(stdout: str, stderr: str, exit_code: int) -> str:
    """Return a stable fingerprint for the most useful failure detail in command output."""
    if exit_code == 0:
        return "success"
    combined = "\n".join(part for part in (stderr, stdout) if part)[-12000:]
    lines = [_normalize_failure_text(line) for line in combined.splitlines()]
    meaningful = [line for line in lines if line and not line.startswith(("Traceback (", "During handling"))]
    for line in reversed(meaningful):
        if any(pattern.search(line) for pattern in _STRUCTURED_FAILURE_PATTERNS):
            return "structured:" + _digest(line.lower())
    compact_tail = " | ".join(meaningful[-8:])[-2000:]
    if compact_tail:
        return "tail:" + _digest(compact_tail.lower())
    return f"exit:{exit_code}"


@dataclass(frozen=True, slots=True)
class PathBoundaries:
    workspace: Path
    private_paths: tuple[Path, ...] = ()

    @classmethod
    def discover(
        cls,
        workspace: Path,
        configured: Iterable[str | Path] = (),
        runtime_paths: Iterable[str | Path] = (),
    ) -> "PathBoundaries":
        workspace = workspace.resolve()
        normalized: list[Path] = []
        for candidate in [*configured, *runtime_paths]:
            if not str(candidate).strip():
                continue
            path = _safe_resolve(candidate)
            if _is_within(path, workspace) or _is_within(workspace, path):
                continue
            if path not in normalized:
                normalized.append(path)
        return cls(workspace=workspace, private_paths=tuple(normalized))

    def classify(self, path: str | Path) -> str:
        resolved = _safe_resolve(path)
        if _is_within(resolved, self.workspace):
            return "workspace"
        if any(_is_within(resolved, private) for private in self.private_paths):
            return "private-runtime"
        return "external/system"

    def contains_private(self, value: str) -> bool:
        if not value:
            return False
        return any(
            piece and self.classify(piece) == "private-runtime"
            for piece in value.split(os.pathsep)
        )


@dataclass(slots=True)
class FileRecord:
    path: str
    path_class: str
    kind: str
    mode: int
    size: int
    mtime_ns: int
    link_target: str | None = None
    content_hash: str | None = None


@dataclass(slots=True)
class Snapshot:
    records: dict[str, FileRecord] = field(default_factory=dict)
    truncated: bool = False
    inspected_files: int = 0


@dataclass(slots=True)
class MutationSummary:
    created: list[FileRecord] = field(default_factory=list)
    modified: list[FileRecord] = field(default_factory=list)
    deleted: list[FileRecord] = field(default_factory=list)
    permissions_changed: list[str] = field(default_factory=list)
    symlinks_created: list[str] = field(default_factory=list)
    directories_modified: list[str] = field(default_factory=list)
    processes_started: list[int] = field(default_factory=list)
    ports_opened: list[str] = field(default_factory=list)

    @property
    def path_classes(self) -> set[str]:
        return {item.path_class for item in [*self.created, *self.modified, *self.deleted]}

    def has_effects(self) -> bool:
        return any(asdict(self).values())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact(self) -> list[str]:
        entries: list[str] = []
        groups = (
            ("created", self.created), ("modified", self.modified), ("deleted", self.deleted)
        )
        for action, records in groups:
            for record in records:
                entries.append(f"{action}: {record.path}")
        entries.extend(f"permissions changed: {path}" for path in self.permissions_changed)
        entries.extend(f"symlink created: {path}" for path in self.symlinks_created)
        if self.processes_started:
            entries.append(f"processes started: {len(self.processes_started)}")
        if self.ports_opened:
            entries.append(f"network listeners changed: {len(self.ports_opened)}")
        if len(entries) > MAX_AGENT_MUTATION_ENTRIES:
            entries = entries[:MAX_AGENT_MUTATION_ENTRIES] + [TRUNCATION_NOTICE]
        return entries


@dataclass(slots=True)
class ExecutionFingerprint:
    cwd: str
    user: str
    shell: str
    path: str
    environment_names: list[str]
    environment_hash: str
    environment_value_hashes: dict[str, str]
    processes: list[int]
    open_ports: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CommandRecord:
    command: str
    cwd: str
    environment_hash: str
    resolved_executables: list[str]
    exit_code: int
    timed_out: bool
    duration_seconds: float
    before: ExecutionFingerprint
    after: ExecutionFingerprint
    mutations: MutationSummary
    path_classes: list[str]
    depended_on_private_runtime: bool
    used_clean_task_context: bool
    snapshot_truncated: bool = False
    external_or_private_state_may_have_changed: bool = False
    warnings: list[str] = field(default_factory=list)
    no_progress_warning: bool = False
    force_finalization: bool = False
    failure_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ValidationEvidence:
    command: str
    label: str
    strength: int
    context_hash: str
    clean_task_context: bool
    depended_on_private_runtime: bool
    produced_artifacts: bool
    scope: str
    exit_code: int
    failure_fingerprint: str = ""
    suspicious: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FailureMemory:
    believed_succeeded: str
    final_validation: str
    contradiction: str
    context_differences: list[str]
    files_and_side_effects: dict[str, Any]
    contamination_warnings: list[str]
    strongest_failure_evidence: str
    weakest_success_evidence: str
    timeout_observed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render_compact(self) -> str:
        rows = [
            "Previous attempt failure summary:",
            f"Final status: {self.final_validation or 'failed'}",
            f"Strongest failing evidence: {self.strongest_failure_evidence or 'final validation failure'}",
        ]
        if self.contradiction:
            rows.append(f"Contradiction: {self.contradiction}")
        if self.weakest_success_evidence:
            rows.append(f"Suspicious weak success evidence: {self.weakest_success_evidence}")
        if self.contamination_warnings:
            rows.append(f"Private-runtime warning: {self.contamination_warnings[0]}")
        if self.timeout_observed:
            rows.append(f"Timeout warning: {TIMEOUT_MESSAGE}")
        rows.append(
            "Recommendation: use the clean task context, address the strongest failure, and run a "
            "materially different validation before claiming completion."
        )
        return _cap_text("\n".join(rows), MAX_COMPACT_RETRY_MEMORY_CHARS)

    # Compatibility for callers that previously rendered the full structure.
    def render(self) -> str:
        return self.render_compact()


@dataclass(slots=True)
class CandidateRecord:
    path: str
    source: str
    source_turn: int | None = None
    snippet: str | None = None
    status: str = "unresolved"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    cleared_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AttemptState:
    before: ExecutionFingerprint
    workspace_root: str = ""
    after: ExecutionFingerprint | None = None
    commands: list[CommandRecord] = field(default_factory=list)
    files_created: set[str] = field(default_factory=set)
    files_modified: set[str] = field(default_factory=set)
    files_deleted: set[str] = field(default_factory=set)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    validation_evidence: list[ValidationEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unresolved_failures: list[str] = field(default_factory=list)
    timeouts: int = 0
    snapshot_truncated: bool = False
    no_progress_steps: int = 0
    no_progress_events: int = 0
    repeated_commands: dict[str, int] = field(default_factory=dict)
    repeated_warnings: dict[str, int] = field(default_factory=dict)
    tool_steps: list[dict[str, Any]] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    _last_evidence_signatures: set[str] = field(default_factory=set, repr=False)
    _evidence_repetitions: dict[str, tuple[int, int]] = field(default_factory=dict, repr=False)
    _progress_epoch: int = field(default=0, repr=False)
    _last_command_exit: dict[str, int] = field(default_factory=dict, repr=False)
    _previous_command_had_warning: bool = field(default=False, repr=False)
    candidate_ledger: dict[str, CandidateRecord] = field(default_factory=dict)
    truncated_discovery_events: list[dict[str, Any]] = field(default_factory=list)
    unresolved_candidate_warnings: list[str] = field(default_factory=list)
    current_source_turn: int | None = field(default=None, repr=False)
    _candidate_generation: int = field(default=0, repr=False)
    _warning_generation: int = field(default=-1, repr=False)
    _warning_emissions: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if not self.workspace_root:
            self.workspace_root = self.before.cwd
        self.workspace_root = str(Path(self.workspace_root).resolve(strict=False))

    def normalize_workspace_path(self, path: str | Path, cwd: str | Path | None = None) -> str | None:
        raw = str(path).strip().strip('"\'')
        if not raw:
            return None
        root = Path(self.workspace_root).resolve(strict=False)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            base = Path(cwd).resolve(strict=False) if cwd else root
            candidate = (base / candidate).resolve(strict=False)
            if not _is_within(candidate, root) and cwd:
                candidate = (root / raw).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)
        if not _is_within(candidate, root):
            return None
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        value = relative.as_posix()
        return value if value and value != "." else None

    def record_candidate_file(self, path: str | Path, source: str, snippet: str | None = None, *, cwd: str | Path | None = None) -> CandidateRecord | None:
        normalized = self.normalize_workspace_path(path, cwd=cwd)
        if not normalized:
            return None
        now = time.time()
        compact_snippet = _cap_text(" ".join(str(snippet).split()), MAX_CANDIDATE_SNIPPET_CHARS) if snippet else None
        record = self.candidate_ledger.get(normalized)
        if record is None:
            record = CandidateRecord(
                path=normalized, source=str(source), source_turn=self.current_source_turn,
                snippet=compact_snippet, first_seen=now, last_seen=now,
            )
            self.candidate_ledger[normalized] = record
            self._candidate_generation += 1
        else:
            record.last_seen = now
            if compact_snippet and not record.snippet:
                record.snippet = compact_snippet
        return record

    def _mark_candidate(self, path: str | Path, status: str, reason: str | None = None, *, cwd: str | Path | None = None) -> None:
        normalized = self.normalize_workspace_path(path, cwd=cwd)
        if not normalized or normalized not in self.candidate_ledger:
            return
        record = self.candidate_ledger[normalized]
        record.status = status
        record.last_seen = time.time()
        if status == "cleared":
            record.cleared_reason = reason or "cleared with evidence"

    def mark_candidate_inspected(self, path: str | Path, *, cwd: str | Path | None = None) -> None:
        normalized = self.normalize_workspace_path(path, cwd=cwd)
        if normalized and normalized in self.candidate_ledger and self.candidate_ledger[normalized].status == "unresolved":
            self._mark_candidate(normalized, "inspected")

    def mark_candidate_modified(self, path: str | Path, *, cwd: str | Path | None = None) -> None:
        self._mark_candidate(path, "modified", cwd=cwd)

    def mark_candidate_cleared(self, path: str | Path, reason: str) -> None:
        self._mark_candidate(path, "cleared", reason)

    def unresolved_candidates(self, limit: int = 10) -> list[CandidateRecord]:
        return [record for record in self.candidate_ledger.values() if record.status == "unresolved"][:limit]

    def candidate_coverage_summary(self) -> dict[str, Any]:
        counts = {status: 0 for status in ("unresolved", "inspected", "modified", "cleared")}
        for record in self.candidate_ledger.values():
            counts[record.status] = counts.get(record.status, 0) + 1
        return {"total": len(self.candidate_ledger), **counts, "truncated_discovery": bool(self.truncated_discovery_events)}

    def mark_truncated_discovery(self, source: str, reason: str) -> None:
        self.truncated_discovery_events.append({
            "source": source, "reason": reason, "turn": self.current_source_turn, "timestamp": time.time()
        })

    def unresolved_candidate_warning(self) -> str:
        unresolved = self.unresolved_candidates(10)
        if not unresolved:
            return ""
        if self._warning_generation == self._candidate_generation and self._warning_emissions >= 2:
            return ""
        if self._warning_generation != self._candidate_generation:
            self._warning_generation = self._candidate_generation
            self._warning_emissions = 0
        rows = [
            "Search discovered potentially relevant files that were not inspected, modified, or cleared. Inspect them, clear them with evidence, or run stronger validation.",
            *[f"- {record.path}" for record in unresolved],
        ]
        if self.truncated_discovery_events:
            rows.append("At least one discovery command was truncated or filtered, so repo-wide completeness is not proven.")
        warning = _cap_text("\n".join(rows), MAX_UNRESOLVED_WARNING_CHARS)
        self._warning_emissions += 1
        self.unresolved_candidate_warnings.append(warning)
        return warning

    def add_command(self, record: CommandRecord) -> None:
        self.commands.append(record)
        for item in record.mutations.created:
            normalized = self.normalize_workspace_path(item.path)
            if normalized:
                self.files_created.add(normalized)
                self.mark_candidate_modified(normalized)
        for item in record.mutations.modified:
            normalized = self.normalize_workspace_path(item.path)
            if normalized:
                self.files_modified.add(normalized)
                self.mark_candidate_modified(normalized)
        for item in record.mutations.deleted:
            normalized = self.normalize_workspace_path(item.path)
            if normalized:
                self.files_deleted.add(normalized)
                self.mark_candidate_modified(normalized)
        if record.mutations.has_effects():
            self.side_effects.append({"command": record.command, **record.mutations.to_dict()})
            if "workspace" in record.mutations.path_classes:
                self._progress_epoch += 1
        if record.timed_out:
            self.timeouts += 1
        self.snapshot_truncated = self.snapshot_truncated or record.snapshot_truncated
        for warning in record.warnings:
            if warning not in self.warnings:
                self.warnings.append(warning)
            self.repeated_warnings[warning] = self.repeated_warnings.get(warning, 0) + 1

    def register_progress(self, record: CommandRecord, evidence: ValidationEvidence) -> tuple[bool, bool]:
        command_signature = " ".join(record.command.split())
        command_count = self.repeated_commands.get(command_signature, 0) + 1
        self.repeated_commands[command_signature] = command_count
        evidence_signature = "|".join(
            (command_signature, str(evidence.exit_code), evidence.label, evidence.failure_fingerprint)
        )
        previous_count, previous_epoch = self._evidence_repetitions.get(
            evidence_signature, (0, -1)
        )
        evidence_count = previous_count + 1 if previous_epoch == self._progress_epoch else 1
        new_evidence = evidence_count == 1
        self._last_evidence_signatures.add(evidence_signature)
        previous_exit = self._last_command_exit.get(command_signature)
        outcome_changed = previous_exit is not None and previous_exit != evidence.exit_code
        self._last_command_exit[command_signature] = evidence.exit_code
        warning_resolved = self._previous_command_had_warning and not record.warnings
        self._previous_command_had_warning = bool(record.warnings)
        workspace_effect = "workspace" in record.mutations.path_classes and record.mutations.has_effects()
        process_effect = bool(record.mutations.processes_started or record.mutations.ports_opened)
        changed_strategy = command_count == 1
        progress = (
            workspace_effect or process_effect or new_evidence or changed_strategy
            or outcome_changed or warning_resolved
        )
        if progress:
            # Invalidate stale repetition pressure. The current evidence starts a fresh epoch so
            # the immediately following identical result can still be counted as a repeat.
            self._progress_epoch += 1
            evidence_count = 1
        self._evidence_repetitions[evidence_signature] = (evidence_count, self._progress_epoch)
        repeated_warning = any(
            value >= NO_PROGRESS_MAX_REPEATED_WARNINGS for value in self.repeated_warnings.values()
        )
        repeated_command = evidence_count >= NO_PROGRESS_MAX_REPEATED_COMMANDS
        if progress and not repeated_warning:
            self.no_progress_steps = 0
            return False, False
        self.no_progress_steps += 1
        threshold = (
            self.no_progress_steps >= NO_PROGRESS_MAX_STEPS or repeated_command or repeated_warning
        )
        if not threshold:
            return False, False
        self.no_progress_events += 1
        self.no_progress_steps = 0
        record.no_progress_warning = True
        record.force_finalization = self.no_progress_events >= 2
        return True, record.force_finalization

    def add_evidence(self, evidence: ValidationEvidence) -> None:
        self.validation_evidence.append(evidence)
        successful = [item for item in self.validation_evidence if item.exit_code == 0]
        failures = [item for item in self.validation_evidence if item.exit_code != 0]
        if successful and failures:
            strongest_success = max(successful, key=lambda item: item.strength)
            strongest_failure = max(failures, key=lambda item: item.strength)
            if strongest_failure.strength > strongest_success.strength:
                strongest_success.suspicious = True
                message = (
                    "Contradictory validation evidence: a weaker check passed in a different or "
                    "polluted context while a stronger clean-context check failed."
                )
                if message not in self.unresolved_failures:
                    self.unresolved_failures.append(message)

    def compact_summary(self) -> str:
        summary = {
            "files_created": len(self.files_created),
            "files_modified": len(self.files_modified),
            "files_deleted": len(self.files_deleted),
            "commands": len(self.commands),
            "failed_commands": sum(item.exit_code != 0 for item in self.commands),
            "timeouts": self.timeouts,
            "evidence": [item.label for item in self.validation_evidence[-3:]],
            "warnings": self.warnings[:2],
            "unresolved": self.unresolved_failures[:2],
        }
        return _cap_text(json.dumps(summary, sort_keys=True), MAX_AGENT_ATTEMPT_STATE_SUMMARY_CHARS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.to_dict(),
            "after": self.after.to_dict() if self.after else None,
            "commands": [item.to_dict() for item in self.commands],
            "files_created": sorted(self.files_created),
            "files_modified": sorted(self.files_modified),
            "files_deleted": sorted(self.files_deleted),
            "side_effects": self.side_effects,
            "validation_evidence": [item.to_dict() for item in self.validation_evidence],
            "warnings": self.warnings,
            "unresolved_failures": self.unresolved_failures,
            "timeouts": self.timeouts,
            "snapshot_truncated": self.snapshot_truncated,
            "no_progress_steps": self.no_progress_steps,
            "no_progress_events": self.no_progress_events,
            "tool_steps": self.tool_steps,
            "tool_failures": self.tool_failures,
            "candidate_ledger": [record.to_dict() for record in self.candidate_ledger.values()],
            "candidate_coverage_summary": self.candidate_coverage_summary(),
            "truncated_discovery_events": self.truncated_discovery_events,
            "unresolved_candidate_warnings": self.unresolved_candidate_warnings,
        }

    def build_failure_memory(self, believed_succeeded: str, final_validation: str) -> FailureMemory:
        successful = [item for item in self.validation_evidence if item.exit_code == 0]
        failures = [item for item in self.validation_evidence if item.exit_code != 0]
        weakest_success = min(successful, key=lambda item: item.strength).label if successful else "agent assertion"
        strongest_failure = max(failures, key=lambda item: item.strength).label if failures else final_validation
        contexts = sorted(
            {
                "private-runtime dependency" if item.depended_on_private_runtime else "clean task context"
                for item in self.validation_evidence
            }
        )
        contradiction = (
            "Something passed in one context but failed in another, so the execution contexts or assumptions may differ."
            if successful and (failures or final_validation)
            else "Final validation did not confirm the attempted solution."
        )
        return FailureMemory(
            believed_succeeded=believed_succeeded,
            final_validation=final_validation,
            contradiction=contradiction,
            context_differences=contexts,
            files_and_side_effects={
                "created": sorted(self.files_created),
                "modified": sorted(self.files_modified),
                "deleted": sorted(self.files_deleted),
                "side_effects": self.side_effects,
            },
            contamination_warnings=list(self.warnings),
            strongest_failure_evidence=strongest_failure,
            weakest_success_evidence=weakest_success,
            timeout_observed=self.timeouts > 0,
        )


class TaskExecutionContext:
    """Build a clean task environment and retain full command telemetry outside model context."""

    _PRESERVED_NAMES = {
        "HOME", "USER", "LOGNAME", "SHELL", "TERM", "COLORTERM", "LANG", "TZ",
        "TMPDIR", "TMP", "TEMP", "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY",
        "SSH_AUTH_SOCK", "SYSTEMROOT", "COMSPEC", "PATHEXT",
    }
    _PRESERVED_PREFIXES = ("LC_",)

    def __init__(
        self,
        workspace: Path,
        *,
        private_paths: Iterable[str | Path] = (),
        task_environment: Mapping[str, str] | None = None,
        allowed_private_paths: Iterable[str | Path] = (),
        snapshot_excluded_paths: Iterable[str | Path] = (),
    ) -> None:
        runtime_paths: list[str | Path] = []
        for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_ENV_PATH"):
            value = os.environ.get(name)
            if value:
                runtime_paths.append(value)
        self.boundaries = PathBoundaries.discover(workspace, private_paths, runtime_paths)
        self.allowed_private_paths = tuple(_safe_resolve(item) for item in allowed_private_paths)
        self.snapshot_excluded_paths = tuple(_safe_resolve(item) for item in snapshot_excluded_paths)
        self.task_environment = dict(task_environment or {})
        self.environment = self._build_environment(os.environ)
        self._attempt_files_before = Snapshot()
        self.attempt = AttemptState(before=self.fingerprint(workspace, self.environment), workspace_root=str(workspace.resolve()))

    def _allowed_private(self, path: str | Path) -> bool:
        resolved = _safe_resolve(path)
        return any(_is_within(resolved, allowed) for allowed in self.allowed_private_paths)

    def _build_environment(self, source: Mapping[str, str]) -> dict[str, str]:
        env: dict[str, str] = {}
        for name, value in source.items():
            if name == "PATH" or name.startswith(("VILLANI_", "CODEX_")):
                continue
            if name in self._PRESERVED_NAMES or name.startswith(self._PRESERVED_PREFIXES):
                if not self.boundaries.contains_private(value):
                    env[name] = value
        path_entries: list[str] = []
        for entry in source.get("PATH", os.defpath).split(os.pathsep):
            if not entry:
                continue
            if self.boundaries.classify(entry) == "private-runtime" and not self._allowed_private(entry):
                continue
            if entry not in path_entries:
                path_entries.append(entry)
        env["PATH"] = os.pathsep.join(path_entries) or os.defpath
        for name, value in self.task_environment.items():
            if self.boundaries.contains_private(value) and not any(
                self._allowed_private(piece) for piece in value.split(os.pathsep) if piece
            ):
                continue
            env[str(name)] = str(value)
        return env

    def begin_attempt(self) -> AttemptState:
        before = self.fingerprint(self.boundaries.workspace, self.environment)
        self._attempt_files_before = self.snapshot_workspace()
        self.attempt = AttemptState(before=before, workspace_root=str(self.boundaries.workspace), snapshot_truncated=self._attempt_files_before.truncated)
        return self.attempt

    def existed_at_attempt_start(self, path: str | Path) -> bool:
        return str(_safe_resolve(path)) in self._attempt_files_before.records

    def finish_attempt(self) -> AttemptState:
        after = self.fingerprint(self.boundaries.workspace, self.environment)
        current = self.snapshot_workspace()
        self.attempt.after = after
        self.attempt.snapshot_truncated = self.attempt.snapshot_truncated or current.truncated
        cumulative = self._mutation_diff(
            self._attempt_files_before.records, current.records, self.attempt.before, after
        )
        for item in cumulative.created:
            normalized = self.attempt.normalize_workspace_path(item.path)
            if normalized:
                self.attempt.files_created.add(normalized)
                self.attempt.mark_candidate_modified(normalized)
        for item in cumulative.modified:
            normalized = self.attempt.normalize_workspace_path(item.path)
            if normalized:
                self.attempt.files_modified.add(normalized)
                self.attempt.mark_candidate_modified(normalized)
        for item in cumulative.deleted:
            normalized = self.attempt.normalize_workspace_path(item.path)
            if normalized:
                self.attempt.files_deleted.add(normalized)
                self.attempt.mark_candidate_modified(normalized)
        try:
            git = subprocess.run(
                ["git", "diff", "--name-only"], cwd=self.boundaries.workspace,
                env=self.environment, text=True, capture_output=True, timeout=10, check=False,
            )
            if git.returncode == 0:
                for raw_path in git.stdout.splitlines():
                    normalized = self.attempt.normalize_workspace_path(raw_path)
                    if normalized:
                        self.attempt.files_modified.add(normalized)
                        self.attempt.mark_candidate_modified(normalized)
        except (OSError, subprocess.SubprocessError):
            pass
        if cumulative.has_effects():
            self.attempt.side_effects.append({"scope": "cumulative-attempt", **cumulative.to_dict()})
        return self.attempt

    def fingerprint(self, cwd: Path, env: Mapping[str, str]) -> ExecutionFingerprint:
        value_hashes = {name: _digest(value) for name, value in sorted(env.items())}
        return ExecutionFingerprint(
            cwd=str(cwd.resolve()),
            user=getpass.getuser(),
            shell=env.get("SHELL") or os.environ.get("SHELL", ""),
            path=env.get("PATH", ""),
            environment_names=sorted(env),
            environment_hash=_digest(json.dumps(value_hashes, sort_keys=True)),
            environment_value_hashes=value_hashes,
            processes=self._processes(),
            open_ports=self._open_ports(),
        )

    def snapshot_workspace(self) -> Snapshot:
        root = self.boundaries.workspace
        snapshot = Snapshot()
        if not root.exists():
            return snapshot
        for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            directory_names[:] = [
                name
                for name in directory_names
                if name not in DEFAULT_SNAPSHOT_SKIP_DIRS
                and not any(
                    _is_within(current / name, excluded) or _is_within(excluded, current / name)
                    for excluded in self.snapshot_excluded_paths
                )
            ]
            candidates = [*(current / name for name in directory_names), *(current / name for name in file_names)]
            for path in candidates:
                if snapshot.inspected_files >= MAX_SNAPSHOT_FILES:
                    snapshot.truncated = True
                    return snapshot
                snapshot.inspected_files += 1
                try:
                    info = path.lstat()
                except OSError:
                    continue
                kind = "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file"
                content_hash = None
                if kind == "file" and info.st_size <= MAX_SNAPSHOT_FILE_BYTES:
                    try:
                        content_hash = _digest_bytes(path.read_bytes())
                    except OSError:
                        content_hash = None
                resolved = str(path.resolve(strict=False))
                snapshot.records[resolved] = FileRecord(
                    path=resolved,
                    path_class="workspace",
                    kind=kind,
                    mode=stat.S_IMODE(info.st_mode),
                    size=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                    link_target=os.readlink(path) if path.is_symlink() else None,
                    content_hash=content_hash,
                )
        return snapshot

    # Compatibility alias; deliberately workspace-only.
    def snapshot_files(self) -> dict[str, FileRecord]:
        return self.snapshot_workspace().records

    @staticmethod
    def _processes() -> list[int]:
        proc = Path("/proc")
        if not proc.exists():
            return []
        try:
            return sorted(int(item.name) for item in proc.iterdir() if item.name.isdigit())
        except OSError:
            return []

    @staticmethod
    def _open_ports() -> list[str]:
        ports: set[str] = set()
        for name in ("tcp", "tcp6", "udp", "udp6"):
            path = Path("/proc/net") / name
            try:
                rows = path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
            except OSError:
                continue
            for row in rows:
                columns = row.split()
                if len(columns) > 3:
                    ports.add(f"{name}:{columns[1]}:{columns[3]}")
        return sorted(ports)

    def resolved_executables(self, command: str, env: Mapping[str, str]) -> list[str]:
        resolved: list[str] = []
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = []
        expect_command = True
        for token in tokens:
            if token in {";", "&&", "||", "|"}:
                expect_command = True
                continue
            if expect_command and "=" not in token:
                candidate = shutil.which(token, path=env.get("PATH"))
                if candidate and candidate not in resolved:
                    resolved.append(str(_safe_resolve(candidate)))
                expect_command = False
        shell = shutil.which(env.get("SHELL", ""), path=env.get("PATH")) if env.get("SHELL") else None
        if shell and shell not in resolved:
            resolved.insert(0, str(_safe_resolve(shell)))
        return resolved

    def _mutation_diff(
        self,
        before: dict[str, FileRecord],
        after: dict[str, FileRecord],
        before_fp: ExecutionFingerprint,
        after_fp: ExecutionFingerprint,
    ) -> MutationSummary:
        created_keys = after.keys() - before.keys()
        deleted_keys = before.keys() - after.keys()
        common = before.keys() & after.keys()
        modified_keys = {
            key for key in common
            if before[key].kind != "directory"
            and (
                before[key].size, before[key].kind, before[key].link_target, before[key].content_hash
            ) != (
                after[key].size, after[key].kind, after[key].link_target, after[key].content_hash
            )
        }
        permissions = sorted(key for key in common if before[key].mode != after[key].mode)
        return MutationSummary(
            created=[after[key] for key in sorted(created_keys)],
            modified=[after[key] for key in sorted(modified_keys)],
            deleted=[before[key] for key in sorted(deleted_keys)],
            permissions_changed=permissions,
            symlinks_created=sorted(key for key in created_keys if after[key].kind == "symlink"),
            directories_modified=[],
            processes_started=sorted(set(after_fp.processes) - set(before_fp.processes)),
            ports_opened=sorted(set(after_fp.open_ports) - set(before_fp.open_ports)),
        )

    @staticmethod
    def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                return
            except (OSError, ProcessLookupError):
                pass
        try:
            proc.kill()
        except OSError:
            pass

    def _run_process(
        self, command: str, cwd: Path, env: Mapping[str, str], timeout: int
    ) -> tuple[int, str, str, bool]:
        popen_kwargs: dict[str, Any] = {
            "shell": True,
            "cwd": str(cwd),
            "env": dict(env),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return int(proc.returncode or 0), stdout or "", stderr or "", False
        except subprocess.TimeoutExpired as exc:
            partial_stdout = _decode_partial(exc.stdout)
            partial_stderr = _decode_partial(exc.stderr)
            self._kill_process_tree(proc)
            try:
                remaining_stdout, remaining_stderr = proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                self._kill_process_tree(proc)
                remaining_stdout, remaining_stderr = "", ""
            stdout = partial_stdout + _decode_partial(remaining_stdout)
            stderr = partial_stderr + _decode_partial(remaining_stderr)
            return 124, stdout, stderr, True

    def run(
        self, command: str, cwd: Path, timeout: int
    ) -> tuple[subprocess.CompletedProcess[str], CommandRecord]:
        env = dict(self.environment)
        before_fp = self.fingerprint(cwd, env)
        before_snapshot = self.snapshot_workspace()
        executables = self.resolved_executables(command, env)
        started = time.monotonic()
        exit_code, stdout, stderr, timed_out = self._run_process(command, cwd, env, timeout)
        duration = time.monotonic() - started
        after_fp = self.fingerprint(cwd, env)
        after_snapshot = self.snapshot_workspace()
        mutations = self._mutation_diff(
            before_snapshot.records, after_snapshot.records, before_fp, after_fp
        )
        try:
            command_tokens = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            command_tokens = []
        explicit_paths = [token for token in command_tokens if token.startswith(("/", "~"))]
        private_dependency = (
            self.boundaries.classify(cwd) == "private-runtime"
            or any(
                self.boundaries.classify(path) == "private-runtime" and not self._allowed_private(path)
                for path in executables
            )
            or any(
                self.boundaries.classify(path) == "private-runtime" and not self._allowed_private(path)
                for path in explicit_paths
            )
            or any(self.boundaries.contains_private(value) for value in env.values())
        )
        warnings = [PRIVATE_WARNING] if private_dependency else []
        if timed_out:
            warnings.append(TIMEOUT_MESSAGE)
        classes = set(mutations.path_classes)
        classes.update(self.boundaries.classify(path) for path in explicit_paths)
        if mutations.processes_started or mutations.ports_opened:
            classes.add("external/system")
        record = CommandRecord(
            command=command,
            cwd=str(cwd.resolve()),
            environment_hash=before_fp.environment_hash,
            resolved_executables=executables,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=round(duration, 6),
            before=before_fp,
            after=after_fp,
            mutations=mutations,
            path_classes=sorted(classes),
            depended_on_private_runtime=private_dependency,
            used_clean_task_context=not private_dependency,
            snapshot_truncated=before_snapshot.truncated or after_snapshot.truncated,
            external_or_private_state_may_have_changed=True,
            warnings=warnings,
            failure_fingerprint=failure_fingerprint(stdout, stderr, exit_code),
        )
        self.attempt.add_command(record)
        completed = subprocess.CompletedProcess(command, exit_code, stdout, stderr)
        return completed, record

    def record_tool_step(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        *,
        is_error: bool,
    ) -> tuple[bool, bool]:
        signature = f"tool:{tool_name}:{json.dumps(dict(tool_input), sort_keys=True, default=str)}"
        count = self.attempt.repeated_commands.get(signature, 0) + 1
        self.attempt.repeated_commands[signature] = count
        self.attempt.tool_steps.append(
            {"tool": tool_name, "input_hash": _digest(signature), "is_error": is_error}
        )
        if is_error:
            failure_signature = f"{tool_name}:{_digest(signature)}"
            if failure_signature not in self.attempt.tool_failures:
                self.attempt.tool_failures.append(failure_signature)
        if count == 1:
            self.attempt._progress_epoch += 1
            self.attempt.no_progress_steps = 0
            return False, False
        if not is_error and tool_name in {"Write", "Patch"}:
            self.attempt._progress_epoch += 1
            self.attempt.no_progress_steps = 0
            return False, False
        self.attempt.no_progress_steps += 1
        if (
            count < NO_PROGRESS_MAX_REPEATED_COMMANDS
            and self.attempt.no_progress_steps < NO_PROGRESS_MAX_STEPS
        ):
            return False, False
        self.attempt.no_progress_events += 1
        self.attempt.no_progress_steps = 0
        return True, self.attempt.no_progress_events >= 2

    def record_final_result(self, summary: str, succeeded: bool) -> ValidationEvidence:
        evidence = ValidationEvidence(
            command=summary or "final validation",
            label="official/verifier result",
            strength=5,
            context_hash=(
                self.attempt.after.environment_hash if self.attempt.after else self.attempt.before.environment_hash
            ),
            clean_task_context=True,
            depended_on_private_runtime=False,
            produced_artifacts=False,
            scope="final expected behaviour",
            exit_code=0 if succeeded else 1,
        )
        self.attempt.add_evidence(evidence)
        return evidence

    def record_validation(
        self,
        record: CommandRecord,
        *,
        kind: str = "smoke",
        final_behavior: bool = False,
        official: bool = False,
    ) -> ValidationEvidence:
        produced_artifacts = bool(record.mutations.created or record.mutations.modified)
        if official:
            label, strength = "official/verifier result", 5
        elif kind == "project" and record.used_clean_task_context:
            label, strength = "project/task tests in clean task context", 4
        elif kind == "smoke" and record.used_clean_task_context:
            label, strength = "independent smoke test in clean task context", 3
        elif record.depended_on_private_runtime:
            label, strength = "smoke test in polluted/private context", 2
        else:
            label, strength = "command exit code only", 1
        evidence = ValidationEvidence(
            command=record.command,
            label=label,
            strength=strength,
            context_hash=record.environment_hash,
            clean_task_context=record.used_clean_task_context,
            depended_on_private_runtime=record.depended_on_private_runtime,
            produced_artifacts=produced_artifacts,
            scope="final expected behaviour" if final_behavior else "partial behaviour",
            exit_code=record.exit_code,
            failure_fingerprint=record.failure_fingerprint,
        )
        self.attempt.add_evidence(evidence)
        warning, force = self.attempt.register_progress(record, evidence)
        record.warnings = [
            item
            for item in record.warnings
            if item == TIMEOUT_MESSAGE or self.attempt.repeated_warnings.get(item, 0) <= NO_PROGRESS_MAX_REPEATED_WARNINGS
        ]
        if warning:
            record.warnings.append(NO_PROGRESS_MESSAGE)
            candidate_warning = self.attempt.unresolved_candidate_warning()
            if candidate_warning:
                record.warnings.append(candidate_warning)
            if NO_PROGRESS_MESSAGE not in self.attempt.warnings:
                self.attempt.warnings.append(NO_PROGRESS_MESSAGE)
        record.force_finalization = force
        return evidence


def compact_command_observation(
    *,
    command: str,
    record: CommandRecord,
    stdout: str,
    stderr: str,
    evidence: ValidationEvidence | None,
) -> dict[str, Any]:
    warnings = list(dict.fromkeys(record.warnings))[:MAX_AGENT_WARNING_COUNT]
    mutation_entries = record.mutations.compact()
    observation: dict[str, Any] = {
        "command": _cap_text(command, 2000),
        "exit_code": record.exit_code,
        "timed_out": record.timed_out,
        "stdout": _cap_text(stdout, MAX_AGENT_STDOUT_CHARS),
        "stderr": _cap_text(stderr, MAX_AGENT_STDERR_CHARS),
    }
    if warnings:
        observation["warnings"] = _cap_text("\n".join(warnings), MAX_AGENT_WARNING_CHARS).splitlines()
    if mutation_entries:
        observation["mutation_summary"] = _cap_text(
            "\n".join(mutation_entries), MAX_AGENT_MUTATION_SUMMARY_CHARS
        ).splitlines()
    if evidence is not None:
        observation["evidence"] = evidence.label
    if record.timed_out:
        observation["message"] = TIMEOUT_MESSAGE
    if warnings or record.exit_code != 0 or record.no_progress_warning:
        if record.no_progress_warning:
            observation["next_action"] = NO_PROGRESS_MESSAGE
        elif record.timed_out:
            observation["next_action"] = "Use a bounded command or inspect partial output before retrying."
        elif record.depended_on_private_runtime:
            observation["next_action"] = "Repeat the check in the clean task context."
        elif record.exit_code != 0:
            observation["next_action"] = "Use the failure output to make a materially different next step."
    return _fit_agent_observation(observation)


def _fit_agent_observation(observation: dict[str, Any]) -> dict[str, Any]:
    def encoded() -> str:
        return json.dumps(observation, ensure_ascii=False)

    if len(encoded()) <= MAX_AGENT_TOOL_RESULT_CHARS:
        return observation
    if "mutation_summary" in observation:
        observation["mutation_summary"] = [TRUNCATION_NOTICE]
    if len(encoded()) <= MAX_AGENT_TOOL_RESULT_CHARS:
        return observation
    warnings = list(observation.get("warnings", []))
    if len(warnings) > 2:
        observation["warnings"] = warnings[:2] + [TRUNCATION_NOTICE]
    if len(encoded()) <= MAX_AGENT_TOOL_RESULT_CHARS:
        return observation
    for output_field in ("stderr", "stdout"):
        value = str(observation.get(output_field, ""))
        overflow = len(encoded()) - MAX_AGENT_TOOL_RESULT_CHARS
        if overflow > 0 and value:
            observation[output_field] = _cap_text(value, max(80, len(value) - overflow - 64))
        if len(encoded()) <= MAX_AGENT_TOOL_RESULT_CHARS:
            return observation
    # Preserve mandatory status fields even under unusual serialization overhead.
    for output_field in ("stderr", "stdout"):
        if len(encoded()) > MAX_AGENT_TOOL_RESULT_CHARS:
            observation[output_field] = TRUNCATION_NOTICE
    return observation
