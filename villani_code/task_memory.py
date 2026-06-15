from __future__ import annotations

import json
import logging
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

LOGGER = logging.getLogger(__name__)
JSONL_FILES = (
    "command_history.jsonl",
    "file_inspection.jsonl",
    "code_changes.jsonl",
    "test_signals.jsonl",
    "hypotheses.jsonl",
    "dead_ends.jsonl",
)
SEARCH_FILES = ("repo_summary.md", "current_state.md", *JSONL_FILES)
VALIDATION_WORDS = re.compile(r"(?:^|[\s./_-])(test|pytest|check|lint|build|compile|ctest)(?:$|[\s./_-])", re.I)
BUILD_FILES = (
    "package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "Makefile", "CMakeLists.txt", "Dockerfile",
)


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    limit: int = Field(default=10, ge=1, le=25)


class MemoryLimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=5, ge=1, le=25)


class MemoryNoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryHypothesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis: str
    status: str = Field(
        description="Hypothesis status. Use one of: active, confirmed, rejected, superseded. Use confirmed for verified/proven hypotheses."
    )
    evidence: str


class MemoryDeadEndInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: str
    why_failed: str
    avoid: str


MEMORY_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "memory_get_current_state": MemoryNoInput,
    "memory_search": MemorySearchInput,
    "memory_record_hypothesis": MemoryHypothesisInput,
    "memory_record_dead_end": MemoryDeadEndInput,
}


def memory_tool_specs() -> list[dict[str, Any]]:
    descriptions = {
        "memory_get_current_state": (
            "Get the compact task-scoped state for this run: repo summary, inspected files, commands run, "
            "changes made, current failures, active hypotheses, dead ends, and next best action. Use only "
            "when this information is likely to change your next action."
        ),
        "memory_search": (
            "Search task-scoped memory for prior commands, failures, file inspections, changed files, "
            "hypotheses, dead ends, paths, errors, or decisions. Use only when looking up prior state would "
            "avoid repeated work."
        ),
        "memory_record_hypothesis": (
            "Record a useful debugging hypothesis and evidence. Status must be active, confirmed, rejected, "
            "or superseded. Use confirmed for verified/proven hypotheses."
        ),
        "memory_record_dead_end": (
            "Record a failed approach, failed patch, repeated failure, or command path that should not be repeated."
        ),
    }
    return [
        {
            "name": name,
            "description": descriptions[name],
            "input_schema": model.model_json_schema(),
        }
        for name, model in MEMORY_TOOL_MODELS.items()
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, limit: int = 4000) -> str:
    text = str(value or "").encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    return text[-limit:]


class TaskMemory:
    def __init__(self, repo: Path, run_id: str, update_interval_tool_calls: int = 5):
        self.repo = repo.resolve()
        self.run_id = run_id
        self.run_dir = self.repo / ".villani" / "memory" / run_id
        self.update_interval_tool_calls = max(1, int(update_interval_tool_calls))
        self.records_written = 0
        self.memory_tool_calls = 0
        self.memory_search_calls = 0
        self.file_reopen_count = 0
        self.duplicate_command_count = 0
        self.duplicate_failed_attempt_count = 0
        self._tool_calls = 0
        self._inspection_counts: Counter[str] = Counter()
        self._command_counts: Counter[str] = Counter()

    def initialize(self) -> None:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            for name in JSONL_FILES:
                (self.run_dir / name).touch(exist_ok=True)
            (self.run_dir / "repo_summary.md").write_text(self._build_repo_summary(), encoding="utf-8")
            self.regenerate_current_state()
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("Task memory initialization failed: %s", exc)

    def _build_repo_summary(self) -> str:
        entries = sorted(self.repo.iterdir(), key=lambda path: path.name.lower())
        visible = [p for p in entries if p.name not in {".git", ".villani", ".villani_code"}]
        dirs = [p.name for p in visible if p.is_dir()][:20]
        files = [p.name for p in visible if p.is_file()][:30]
        build_files = [name for name in BUILD_FILES if (self.repo / name).exists()]
        readmes = [p.name for p in visible if p.is_file() and (p.name.lower().startswith("readme") or p.suffix.lower() == ".md")][:10]
        test_dirs = [p.name for p in visible if p.is_dir() and ("test" in p.name.lower() or p.name.lower() in {"spec", "specs"})]
        suffixes = Counter(p.suffix.lower() for p in self.repo.glob("*.*") if p.is_file() and p.suffix)
        language_map = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".rs": "Rust", ".go": "Go", ".java": "Java", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".c": "C"}
        languages = [language_map[suffix] for suffix, _ in suffixes.most_common() if suffix in language_map][:5]
        project_type = ", ".join(build_files) if build_files else "Unknown"
        return "\n".join([
            f"Project type: {project_type}",
            f"Main languages: {', '.join(languages) if languages else 'Unknown'}",
            "Build/test commands discovered: Unknown",
            f"Important directories: {', '.join(dirs + test_dirs) if dirs or test_dirs else 'Unknown'}",
            f"Important files: {', '.join(dict.fromkeys(build_files + readmes + files)) if files or build_files else 'Unknown'}",
            "Entry points: Unknown",
            "Known constraints: Unknown",
            "",
        ])

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with (self.run_dir / filename).open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self.records_written += 1
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("Task memory write failed for %s: %s", filename, exc)

    def _read_records(self, filename: str) -> list[dict[str, Any]]:
        try:
            rows = []
            for line in (self.run_dir / filename).read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
                except json.JSONDecodeError:
                    continue
            return rows
        except Exception as exc:
            LOGGER.warning("Task memory read failed for %s: %s", filename, exc)
            return []

    def record_command(self, *, command: str, cwd: str, exit_code: int | None, stdout: str, stderr: str, duration_ms: int | None = None) -> None:
        normalized = " ".join(command.split())
        self._command_counts[normalized] += 1
        if self._command_counts[normalized] > 1:
            self.duplicate_command_count += 1
        stdout_tail, stderr_tail = _safe_text(stdout), _safe_text(stderr)
        if exit_code not in (0, None):
            summary = "command failed"
        elif stderr_tail:
            summary = "command produced stderr"
        elif not stdout_tail:
            summary = "command produced no output"
        elif self.is_validation_command(command):
            summary = "command appears to have test/build/check output"
        else:
            summary = "command succeeded"
        record = {"ts": _now(), "command": command, "cwd": cwd, "exit_code": exit_code, "stdout_tail": stdout_tail, "stderr_tail": stderr_tail, "duration_ms": duration_ms, "summary": summary}
        self._append("command_history.jsonl", record)
        if self.is_validation_command(command):
            clues = _safe_text("\n".join(part for part in (stdout_tail, stderr_tail) if part), 1200)
            signal_summary = summary if not clues else f"{summary}: {clues}"
            self._append("test_signals.jsonl", {"ts": _now(), "command": command, "passed": exit_code == 0, "exit_code": exit_code, "summary": signal_summary, "failures": []})

    @staticmethod
    def is_validation_command(command: str) -> bool:
        lowered = " ".join(command.lower().split())
        explicit = ("npm test", "yarn test", "pnpm test", "go test", "cargo test", "mvn test", "gradle test", "make test")
        return any(item in lowered for item in explicit) or bool(VALIDATION_WORDS.search(lowered))

    def record_inspection(self, path: str) -> None:
        normalized = path.replace("\\", "/")
        self._inspection_counts[normalized] += 1
        if self._inspection_counts[normalized] > 1:
            self.file_reopen_count += 1
        self._append("file_inspection.jsonl", {"ts": _now(), "path": normalized, "reason": "inspected by agent", "summary": "No summary yet", "symbols": []})

    def record_change(self, path: str, change_type: str = "edit", summary: str | None = None) -> None:
        self._append("code_changes.jsonl", {"ts": _now(), "path": path.replace("\\", "/"), "change_type": change_type, "summary": summary or f"file {change_type}", "reason": None, "related_signal": None})

    def _record_shell_inspections(self, command: str, cwd: str) -> None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return
        inspection_commands = {"cat", "head", "tail", "less", "more", "grep", "rg", "sed", "awk"}
        if not any(Path(token).name in inspection_commands for token in tokens):
            return
        base = (self.repo / cwd).resolve()
        seen: set[str] = set()
        for token in tokens:
            if not token or token.startswith("-") or token in seen:
                continue
            candidate = Path(token)
            resolved = candidate if candidate.is_absolute() else base / candidate
            if resolved.is_file():
                seen.add(token)
                try:
                    display = str(resolved.resolve().relative_to(self.repo))
                except ValueError:
                    display = str(resolved)
                self.record_inspection(display)

    def observe_tool_result(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        result: dict[str, Any],
        *,
        target_existed_before: bool | None = None,
    ) -> None:
        try:
            if tool_name == "Read" and tool_input.get("file_path"):
                self.record_inspection(str(tool_input["file_path"]))
            elif tool_name == "Bash":
                command = str(tool_input.get("command", ""))
                cwd = str(tool_input.get("cwd", "."))

                stdout = ""
                stderr = ""
                exit_code: int | None = None

                try:
                    decoded = json.loads(str(result.get("content", "")))
                    if isinstance(decoded, dict):
                        stdout = str(decoded.get("stdout", ""))
                        stderr = str(decoded.get("stderr", ""))
                        raw_exit_code = decoded.get("exit_code")
                        if isinstance(raw_exit_code, int):
                            exit_code = raw_exit_code
                        command = str(decoded.get("command", command))
                except Exception:
                    # Keep a useful failure record even if the Bash result is not JSON.
                    stdout = str(result.get("content", ""))

                if result.get("is_error") and exit_code is None:
                    exit_code = 1
                    stderr = stderr or str(result.get("content", ""))

                self.record_command(
                    command=command,
                    cwd=cwd,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=None,
                )

                self._record_shell_inspections(command, cwd)
            elif tool_name in {"Grep", "Search"}:
                content = str(result.get("content", ""))
                seen: set[str] = set()
                for line in content.splitlines()[:500]:
                    candidate = line.split(":", 1)[0].strip()
                    path = Path(candidate)
                    if candidate and candidate not in seen and ((path.is_absolute() and path.is_file()) or (self.repo / path).is_file()):
                        seen.add(candidate)
                        self.record_inspection(candidate)
            elif tool_name == "Write" and tool_input.get("file_path") and not result.get("is_error"):
                self.record_change(
                    str(tool_input["file_path"]),
                    "edit" if target_existed_before else "create",
                )
            elif tool_name == "Patch" and not result.get("is_error"):
                diff = str(tool_input.get("unified_diff", ""))
                old_paths = re.findall(r"^---\s+(?:a/)?(.+)$", diff, re.M)
                new_paths = re.findall(r"^\+\+\+\s+(?:b/)?(.+)$", diff, re.M)
                pairs = list(zip(old_paths, new_paths))
                if not pairs and tool_input.get("file_path"):
                    pairs = [(str(tool_input["file_path"]), str(tool_input["file_path"]))]
                for old_path, new_path in pairs:
                    if old_path == "/dev/null":
                        self.record_change(new_path, "create", "patch applied")
                    elif new_path == "/dev/null":
                        self.record_change(old_path, "delete", "patch applied")
                    elif old_path != new_path:
                        self.record_change(new_path, "rename", "patch applied")
                    else:
                        self.record_change(new_path, "edit", "patch applied")
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("Task memory tool observation failed: %s", exc)

    def after_tool_call(self) -> None:
        self._tool_calls += 1
        if self._tool_calls % self.update_interval_tool_calls == 0:
            self.regenerate_current_state()

    def execute_tool(self, name: str, raw_input: dict[str, Any]) -> dict[str, Any]:
        self.memory_tool_calls += 1
        model = MEMORY_TOOL_MODELS.get(name)
        if model is None:
            return {"content": f"Unknown memory tool: {name}", "is_error": True}
        try:
            data = model.model_validate(raw_input)
            if name == "memory_search":
                self.memory_search_calls += 1
                content = self.search(data.query, data.limit)
            elif name == "memory_get_repo_summary":
                content = self._read_compact("repo_summary.md", 8000)
            elif name == "memory_get_current_state":
                content = self._read_compact("current_state.md", 8000)
            elif name == "memory_recent_commands":
                content = self._format_records(self._read_records("command_history.jsonl")[-data.limit:])
            elif name == "memory_recent_failures":
                failed_signals = [
                    {"source": "test_signals", **row}
                    for row in self._read_records("test_signals.jsonl")
                    if not row.get("passed")
                ]
                failed_commands = [
                    {"source": "command_history", **row}
                    for row in self._read_records("command_history.jsonl")
                    if row.get("exit_code") not in (0, None)
                ]
                failed = sorted(
                    failed_signals + failed_commands,
                    key=lambda row: str(row.get("ts", "")),
                )
                content = self._format_records(failed[-data.limit:])
            elif name == "memory_changed_files":
                content = self._format_records(self._read_records("code_changes.jsonl")[-data.limit:])
            elif name == "memory_inspected_files":
                content = self._format_records(self._read_records("file_inspection.jsonl")[-data.limit:])
            elif name == "memory_record_hypothesis":
                status_aliases = {
                    "verified": "confirmed",
                    "proven": "confirmed",
                    "true": "confirmed",
                    "done": "confirmed",
                    "failed": "rejected",
                    "false": "rejected",
                    "invalid": "rejected",
                    "wrong": "rejected",
                    "replaced": "superseded",
                }
                status = str(data.status or "").strip().lower()
                status = status_aliases.get(status, status)

                if status not in {"active", "confirmed", "rejected", "superseded"}:
                    raise ValueError("status must be active, confirmed, rejected, or superseded")

                self._append(
                    "hypotheses.jsonl",
                    {
                        "ts": _now(),
                        "hypothesis": data.hypothesis,
                        "status": status,
                        "evidence": data.evidence,
                    },
                )
                content = "Hypothesis recorded."
            else:
                self._append("dead_ends.jsonl", {"ts": _now(), "attempt": data.attempt, "why_failed": data.why_failed, "avoid": data.avoid})
                content = "Dead end recorded."
            return {"content": content or "No matching memory records.", "is_error": False}
        except Exception as exc:
            LOGGER.warning("Task memory tool failed: %s", exc)
            return {"content": f"Memory tool unavailable: {exc}", "is_error": True}

    def _read_compact(self, filename: str, max_chars: int) -> str:
        try:
            return (self.run_dir / filename).read_text(encoding="utf-8", errors="replace")[:max_chars]
        except Exception as exc:
            LOGGER.warning("Task memory artifact read failed: %s", exc)
            return ""

    @staticmethod
    def _format_records(records: list[dict[str, Any]]) -> str:
        if not records:
            return "No matching memory records."
        return "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in records)

    def search(self, query: str, limit: int = 10) -> str:
        terms = [term.lower() for term in re.findall(r"\w+", query) if term]
        if not terms:
            return ""
        matches: list[str] = []
        for filename in SEARCH_FILES:
            try:
                lines = (self.run_dir / filename).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, start=1):
                lowered = line.lower()
                if all(term in lowered for term in terms):
                    matches.append(f"{filename}:{index}: {_safe_text(line, 600)}")
                    if len(matches) >= limit:
                        return "\n".join(matches)
        return "\n".join(matches)

    def regenerate_current_state(self) -> None:
        try:
            repo_lines = [line for line in self._read_compact("repo_summary.md", 3000).splitlines() if line][:4]
            inspected = self._read_records("file_inspection.jsonl")[-8:]
            commands = self._read_records("command_history.jsonl")[-6:]
            changes = self._read_records("code_changes.jsonl")[-8:]
            failed_signals = [
                {"source": "test_signals", **row}
                for row in self._read_records("test_signals.jsonl")
                if not row.get("passed")
            ]
            failed_commands = [
                {"source": "command_history", **row}
                for row in self._read_records("command_history.jsonl")
                if row.get("exit_code") not in (0, None)
            ]
            failures = sorted(
                failed_signals + failed_commands,
                key=lambda row: str(row.get("ts", "")),
            )[-5:]
            hypotheses = [row for row in self._read_records("hypotheses.jsonl") if row.get("status") == "active"][-5:]
            dead_ends = self._read_records("dead_ends.jsonl")[-5:]
            bullet = lambda rows, field, fallback="None": [f"* {_safe_text(row.get(field, ''), 300)}" for row in rows] or [f"* {fallback}"]
            next_action = "Inspect the most relevant unexamined file or run the narrowest useful verification."
            if failures:
                next_action = "Use the latest validation failure as evidence before making or retrying changes."
            command_lines = [f"* {_safe_text(row.get('command', ''), 220)} — {row.get('summary', '')}" for row in commands] or ["* None"]
            content = "\n".join([
                "# Current State", "", "## Objective", "", "Fix the current task according to the user/request instructions.", "",
                "## Repo summary", "", *[f"* {line}" for line in repo_lines], "",
                "## Files inspected", "", *bullet(inspected, "path"), "",
                "## Commands run", "", *command_lines, "",
                "## Changes made", "", *bullet(changes, "path"), "",
                "## Current failures", "", *bullet(failures, "summary"), "",
                "## Active hypotheses", "", *bullet(hypotheses, "hypothesis"), "",
                "## Dead ends", "", *bullet(dead_ends, "attempt"), "",
                "## Next best action", "", f"* {next_action}", "",
            ])
            (self.run_dir / "current_state.md").write_text(content[:6000], encoding="utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("Task memory state update failed: %s", exc)

    def telemetry(self) -> dict[str, Any]:
        current_state = self._read_compact("current_state.md", 10000)
        return {
            "memory_enabled": True,
            "memory_run_dir": str(self.run_dir),
            "memory_records_written": self.records_written,
            "memory_tool_calls": self.memory_tool_calls,
            "memory_search_calls": self.memory_search_calls,
            "memory_current_state_tokens": max(0, len(current_state) // 4),
            "file_reopen_count": self.file_reopen_count,
            "duplicate_command_count": self.duplicate_command_count,
            "duplicate_failed_attempt_count": self.duplicate_failed_attempt_count,
        }
