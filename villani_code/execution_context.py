from __future__ import annotations

import getpass
import hashlib
import json
import os
import shlex
import shutil
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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _safe_resolve(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


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
        candidates = [*configured, *runtime_paths]
        normalized: list[Path] = []
        for candidate in candidates:
            if not str(candidate).strip():
                continue
            path = _safe_resolve(candidate)
            # A source checkout may contain the runner and the task. The workspace always wins.
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
        for piece in value.split(os.pathsep):
            if piece and self.classify(piece) == "private-runtime":
                return True
        return False


@dataclass(slots=True)
class FileRecord:
    path: str
    path_class: str
    kind: str
    mode: int
    size: int
    mtime_ns: int
    link_target: str | None = None


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    duration_seconds: float
    before: ExecutionFingerprint
    after: ExecutionFingerprint
    mutations: MutationSummary
    path_classes: list[str]
    depended_on_private_runtime: bool
    used_clean_task_context: bool
    warnings: list[str] = field(default_factory=list)

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        return "Previous attempt failure memory:\n" + json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass(slots=True)
class AttemptState:
    before: ExecutionFingerprint
    after: ExecutionFingerprint | None = None
    commands: list[CommandRecord] = field(default_factory=list)
    files_created: set[str] = field(default_factory=set)
    files_modified: set[str] = field(default_factory=set)
    files_deleted: set[str] = field(default_factory=set)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    validation_evidence: list[ValidationEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unresolved_failures: list[str] = field(default_factory=list)

    def add_command(self, record: CommandRecord) -> None:
        self.commands.append(record)
        for item in record.mutations.created:
            self.files_created.add(item.path)
        for item in record.mutations.modified:
            self.files_modified.add(item.path)
        for item in record.mutations.deleted:
            self.files_deleted.add(item.path)
        mutation = record.mutations.to_dict()
        if any(mutation.values()):
            self.side_effects.append({"command": record.command, **mutation})
        for warning in record.warnings:
            if warning not in self.warnings:
                self.warnings.append(warning)

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
        }

    def build_failure_memory(self, believed_succeeded: str, final_validation: str) -> FailureMemory:
        successful = [item for item in self.validation_evidence if item.exit_code == 0]
        failures = [item for item in self.validation_evidence if item.exit_code != 0]
        weakest_success = min(successful, key=lambda item: item.strength).label if successful else "agent assertion"
        strongest_failure = max(failures, key=lambda item: item.strength).label if failures else final_validation
        context_differences = sorted(
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
            context_differences=context_differences,
            files_and_side_effects={
                "created": sorted(self.files_created),
                "modified": sorted(self.files_modified),
                "deleted": sorted(self.files_deleted),
                "side_effects": self.side_effects,
            },
            contamination_warnings=list(self.warnings),
            strongest_failure_evidence=strongest_failure,
            weakest_success_evidence=weakest_success,
        )


class TaskExecutionContext:
    """Builds clean task environments and records generic command effects."""

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
    ) -> None:
        runtime_paths: list[str | Path] = []
        for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_ENV_PATH"):
            value = os.environ.get(name)
            if value:
                runtime_paths.append(value)
        self.boundaries = PathBoundaries.discover(workspace, private_paths, runtime_paths)
        self.allowed_private_paths = tuple(_safe_resolve(item) for item in allowed_private_paths)
        self.task_environment = dict(task_environment or {})
        self.environment = self._build_environment(os.environ)
        self._attempt_files_before: dict[str, FileRecord] = {}
        self.attempt = AttemptState(before=self.fingerprint(workspace, self.environment))

    def _allowed_private(self, path: str | Path) -> bool:
        resolved = _safe_resolve(path)
        return any(_is_within(resolved, allowed) for allowed in self.allowed_private_paths)

    def _build_environment(self, source: Mapping[str, str]) -> dict[str, str]:
        env: dict[str, str] = {}
        for name, value in source.items():
            if name == "PATH":
                continue
            if name.startswith(("VILLANI_", "CODEX_")):
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
        self._attempt_files_before = self.snapshot_files()
        self.attempt = AttemptState(before=before)
        return self.attempt

    def existed_at_attempt_start(self, path: str | Path) -> bool:
        return str(_safe_resolve(path)) in self._attempt_files_before

    def finish_attempt(self) -> AttemptState:
        after = self.fingerprint(self.boundaries.workspace, self.environment)
        self.attempt.after = after
        cumulative = self._mutation_diff(
            self._attempt_files_before,
            self.snapshot_files(),
            self.attempt.before,
            after,
        )
        for item in cumulative.created:
            self.attempt.files_created.add(item.path)
        for item in cumulative.modified:
            self.attempt.files_modified.add(item.path)
        for item in cumulative.deleted:
            self.attempt.files_deleted.add(item.path)
        cumulative_payload = cumulative.to_dict()
        if any(cumulative_payload.values()):
            self.attempt.side_effects.append({"scope": "cumulative-attempt", **cumulative_payload})
        if "private-runtime" in cumulative.path_classes and PRIVATE_WARNING not in self.attempt.warnings:
            self.attempt.warnings.append(PRIVATE_WARNING)
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

    def _snapshot_root(self, root: Path) -> dict[str, FileRecord]:
        records: dict[str, FileRecord] = {}
        if not root.exists():
            return records
        try:
            paths = [root, *root.rglob("*")]
        except OSError:
            paths = [root]
        for path in paths:
            try:
                info = path.lstat()
            except OSError:
                continue
            mode = stat.S_IMODE(info.st_mode)
            kind = "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file"
            target = os.readlink(path) if path.is_symlink() else None
            records[str(path.resolve(strict=False))] = FileRecord(
                path=str(path.resolve(strict=False)), path_class=self.boundaries.classify(path), kind=kind,
                mode=mode, size=info.st_size, mtime_ns=info.st_mtime_ns, link_target=target,
            )
        return records

    def snapshot_files(self) -> dict[str, FileRecord]:
        result = self._snapshot_root(self.boundaries.workspace)
        for root in self.boundaries.private_paths:
            result.update(self._snapshot_root(root))
        return result

    @staticmethod
    def _processes() -> list[int]:
        proc = Path("/proc")
        if not proc.exists():
            return []
        return sorted(int(item.name) for item in proc.iterdir() if item.name.isdigit())

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
        separators = {";", "&&", "||", "|"}
        for token in tokens:
            if token in separators:
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
            if (before[key].mtime_ns, before[key].size, before[key].kind, before[key].link_target)
            != (after[key].mtime_ns, after[key].size, after[key].kind, after[key].link_target)
        }
        permissions = sorted(key for key in common if before[key].mode != after[key].mode)
        return MutationSummary(
            created=[after[key] for key in sorted(created_keys)],
            modified=[after[key] for key in sorted(modified_keys)],
            deleted=[before[key] for key in sorted(deleted_keys)],
            permissions_changed=permissions,
            symlinks_created=sorted(key for key in created_keys if after[key].kind == "symlink"),
            directories_modified=sorted(key for key in modified_keys if after[key].kind == "directory"),
            processes_started=sorted(set(after_fp.processes) - set(before_fp.processes)),
            ports_opened=sorted(set(after_fp.open_ports) - set(before_fp.open_ports)),
        )

    def run(self, command: str, cwd: Path, timeout: int) -> tuple[subprocess.CompletedProcess[str], CommandRecord]:
        env = dict(self.environment)
        before_fp = self.fingerprint(cwd, env)
        before_files = self.snapshot_files()
        executables = self.resolved_executables(command, env)
        started = time.monotonic()
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
        )
        duration = time.monotonic() - started
        after_fp = self.fingerprint(cwd, env)
        mutations = self._mutation_diff(before_files, self.snapshot_files(), before_fp, after_fp)
        try:
            command_tokens = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            command_tokens = []
        private_dependency = any(
            self.boundaries.classify(path) == "private-runtime" and not self._allowed_private(path)
            for path in executables
        ) or any(
            self.boundaries.classify(piece) == "private-runtime" and not self._allowed_private(piece)
            for piece in command_tokens
            if piece.startswith(("/", "~"))
        )
        private_mutation = "private-runtime" in mutations.path_classes
        warnings = [PRIVATE_WARNING] if private_dependency or private_mutation else []
        classes = set(mutations.path_classes)
        for token in command_tokens:
            if token.startswith(("/", "~")):
                classes.add(self.boundaries.classify(token))
        if mutations.processes_started or mutations.ports_opened:
            classes.add("external/system")
        record = CommandRecord(
            command=command,
            cwd=str(cwd.resolve()),
            environment_hash=before_fp.environment_hash,
            resolved_executables=executables,
            exit_code=proc.returncode,
            duration_seconds=round(duration, 6),
            before=before_fp,
            after=after_fp,
            mutations=mutations,
            path_classes=sorted(classes),
            depended_on_private_runtime=private_dependency,
            used_clean_task_context=not private_dependency,
            warnings=warnings,
        )
        self.attempt.add_command(record)
        return proc, record

    def record_final_result(self, summary: str, succeeded: bool) -> ValidationEvidence:
        evidence = ValidationEvidence(
            command=summary or "final validation",
            label="official/verifier result",
            strength=5,
            context_hash=self.attempt.after.environment_hash if self.attempt.after else self.attempt.before.environment_hash,
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
        )
        self.attempt.add_evidence(evidence)
        return evidence
