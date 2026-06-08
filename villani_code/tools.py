from __future__ import annotations

import glob
import json
import re
import shutil
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from villani_code.execution_context import (
    MAX_AGENT_TOOL_RESULT_CHARS,
    TRUNCATION_NOTICE,
    TaskExecutionContext,
    compact_command_observation,
)
from villani_code.patch_apply import (
    PatchApplyError,
    apply_unified_diff_with_diagnostics,
    extract_unified_diff_targets,
    parse_unified_diff,
)


class LsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = "."
    ignore: list[str] = Field(default_factory=lambda: [".git", ".venv", "__pycache__"])


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str
    max_bytes: int = 200000


class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str
    path: str = "."
    include_hidden: bool = False
    max_results: int = 200


class GlobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str
    max_results: int = 1000


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    path: str = "."
    context_lines: int = 2
    max_results: int = 200


class BashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    cwd: str = "."
    timeout_sec: int = 30
    validation_kind: str = "command"
    checks_final_behavior: bool = False


class WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str
    content: str
    mkdirs: bool = True


class PatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = ""
    unified_diff: str


class WebFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_sec: int = 20


class GitSimpleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    args: list[str] = Field(default_factory=list)


class SubmitPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_summary: str
    candidate_files: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    recommended_steps: list[str]
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: str = "medium"
    confidence_score: float = 0.5


TOOL_MODELS: dict[str, type[BaseModel]] = {
    "Ls": LsInput,
    "Read": ReadInput,
    "Grep": GrepInput,
    "Glob": GlobInput,
    "Search": SearchInput,
    "Bash": BashInput,
    "Write": WriteInput,
    "Patch": PatchInput,
    "WebFetch": WebFetchInput,
    "GitStatus": GitSimpleInput,
    "GitDiff": GitSimpleInput,
    "GitLog": GitSimpleInput,
    "GitBranch": GitSimpleInput,
    "GitCheckout": GitSimpleInput,
    "GitCommit": GitSimpleInput,
    "SubmitPlan": SubmitPlanInput,
}

DENYLIST = ["rm -rf", "del /s", "format ", "mkfs", "dd if=", "curl ", "wget "]
MAX_CANDIDATES_PER_COMMAND = 100
_DISCOVERY_COMMAND_RE = re.compile(r"(?:^|[;&|()]|\s)(?:git\s+grep|grep|rg|find|ls|fd|ag)(?:\s|$)", re.IGNORECASE)
_PARTIAL_PIPE_RE = re.compile(r"\|\s*(?:head|tail|grep|sed|awk)(?:\s|$)", re.IGNORECASE)
_GREP_RESULT_RE = re.compile(r"^(.*?):(?:\d+)(?::|-)(.*)$")


def _error(message: str) -> dict[str, Any]:
    return {"content": _cap_tool_content(message), "is_error": True}


def _cap_tool_content(content: str) -> str:
    if len(content) <= MAX_AGENT_TOOL_RESULT_CHARS:
        return content
    room = max(0, MAX_AGENT_TOOL_RESULT_CHARS - len(TRUNCATION_NOTICE) - 1)
    return content[:room] + "\n" + TRUNCATION_NOTICE


def _ok(content: str, **metadata: Any) -> dict[str, Any]:
    return {"content": _cap_tool_content(content), "is_error": False, **metadata}


def tool_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for name, model in TOOL_MODELS.items():
        specs.append(
            {
                "name": name,
                "description": f"{name} tool for Villani Code.",
                "input_schema": model.model_json_schema(),
            }
        )
    return specs


def execute_tool(
    name: str,
    raw_input: dict[str, Any],
    repo: Path,
    unsafe: bool = False,
    debug_callback: Any | None = None,
    tool_call_id: str = "",
    execution_context: TaskExecutionContext | None = None,
) -> dict[str, Any]:
    model = TOOL_MODELS.get(name)
    if not model:
        return _error(f"Unknown tool: {name}")
    try:
        parsed = model.model_validate(raw_input)
    except Exception as exc:
        return _error(f"Invalid input for {name}: {exc}")

    try:
        if name == "Ls":
            return _ok(_run_ls(parsed, repo))
        if name == "Read":
            return _ok(_run_read(parsed, repo, debug_callback=debug_callback, tool_call_id=tool_call_id, execution_context=execution_context))
        if name == "Grep":
            return _ok(_run_grep(parsed, repo, execution_context=execution_context))
        if name == "Glob":
            return _ok(_run_glob(parsed, repo, execution_context=execution_context))
        if name == "Search":
            return _ok(_run_search(parsed, repo, execution_context=execution_context))
        if name == "Bash":
            content, metadata = _run_bash(
                parsed,
                repo,
                unsafe=unsafe,
                debug_callback=debug_callback,
                tool_call_id=tool_call_id,
                execution_context=execution_context,
            )
            return _ok(content, **metadata)
        if name == "Write":
            return _ok(_run_write(parsed, repo, debug_callback=debug_callback, tool_call_id=tool_call_id, execution_context=execution_context))
        if name == "Patch":
            return _ok(_run_patch(parsed, repo, debug_callback=debug_callback, tool_call_id=tool_call_id, execution_context=execution_context))
        if name == "WebFetch":
            return _ok(_run_webfetch(parsed))
        if name.startswith("Git"):
            return _ok(_run_git(name, parsed, repo, execution_context=execution_context))
        if name == "SubmitPlan":
            return _ok("Plan artifact submitted")
    except Exception as exc:
        return _error(str(exc))
    return _error("Unhandled tool")


def _is_within_workspace(path: Path, repo: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(repo.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _safe_path(repo: Path, raw: str) -> Path:
    path = (repo / raw).resolve()
    repo_resolved = repo.resolve()
    try:
        path.relative_to(repo_resolved)
    except ValueError:
        raise ValueError("Path escapes repository")
    return path


def _run_ls(data: LsInput, repo: Path) -> str:
    target = _safe_path(repo, data.path)
    lines = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if entry.name in data.ignore:
            continue
        lines.append(f"{entry.name}{'/' if entry.is_dir() else ''}")
    return "\n".join(lines)


def _run_read(data: ReadInput, repo: Path, debug_callback: Any | None = None, tool_call_id: str = "", execution_context: TaskExecutionContext | None = None) -> str:
    requested = Path(data.file_path).expanduser()
    if requested.is_absolute() and not _is_within_workspace(requested, repo):
        raise ValueError(
            "Read is workspace-only for this path. Use a shell command such as cat, sed, or head if you need to inspect system files."
        )
    path = _safe_path(repo, data.file_path)
    raw = path.read_bytes()[: data.max_bytes]
    if execution_context is not None:
        execution_context.attempt.mark_candidate_inspected(path)
    if callable(debug_callback):
        debug_callback("file_read", {"file_path": data.file_path, "size_bytes": len(raw), "ok": True, "tool_call_id": tool_call_id})
    return raw.decode("utf-8", errors="replace")


def _run_grep(
    data: GrepInput,
    repo: Path,
    execution_context: TaskExecutionContext | None = None,
) -> str:
    base = _safe_path(repo, data.path)
    context = execution_context or TaskExecutionContext(repo)
    rg_bin = shutil.which("rg", path=context.environment.get("PATH"))
    if rg_bin:
        cmd = [rg_bin, "-n", data.pattern, str(base)]
        if data.include_hidden:
            cmd.append("--hidden")
        proc, record = context.run(shlex.join(cmd), repo, 30)
        result_lines = proc.stdout.splitlines()
        returned_lines = result_lines[: data.max_results]
        for line in returned_lines:
            match = _GREP_RESULT_RE.match(line)
            if match:
                context.attempt.record_candidate_file(match.group(1), "Grep", match.group(2), cwd=repo)
        if len(result_lines) >= data.max_results:
            context.attempt.mark_truncated_discovery("Grep", f"max_results={data.max_results}")
        output = "\n".join(returned_lines)
        if len(output) > MAX_AGENT_TOOL_RESULT_CHARS:
            context.attempt.mark_truncated_discovery("Grep", "tool output exceeded runner cap")
        if record.warnings:
            output = output + ("\n" if output else "") + "\n".join(record.warnings)
        return output
    return ""


def _run_glob(data: GlobInput, repo: Path, execution_context: TaskExecutionContext | None = None) -> str:
    context = execution_context or TaskExecutionContext(repo)
    hits = sorted(str(Path(p).relative_to(repo)) for p in glob.glob(str(repo / data.pattern), recursive=True))
    returned = hits[: data.max_results]
    for path in returned:
        context.attempt.record_candidate_file(path, "Glob")
    if len(hits) > data.max_results:
        context.attempt.mark_truncated_discovery("Glob", f"max_results={data.max_results}")
    output = "\n".join(returned)
    if len(output) > MAX_AGENT_TOOL_RESULT_CHARS:
        context.attempt.mark_truncated_discovery("Glob", "tool output exceeded runner cap")
    return output


def _run_search(
    data: SearchInput,
    repo: Path,
    execution_context: TaskExecutionContext | None = None,
) -> str:
    context = execution_context or TaskExecutionContext(repo)
    rg_bin = shutil.which("rg", path=context.environment.get("PATH"))
    if not rg_bin:
        return _run_grep(GrepInput(pattern=data.query, path=data.path), repo, execution_context=context)
    base = _safe_path(repo, data.path)
    cmd = [rg_bin, "-n", "-C", str(data.context_lines), data.query, str(base)]
    proc, record = context.run(shlex.join(cmd), repo, 30)
    result_lines = proc.stdout.splitlines()
    matching_lines = [line for line in result_lines if _GREP_RESULT_RE.match(line)]
    returned_matches = matching_lines[: data.max_results]
    for line in returned_matches:
        match = _GREP_RESULT_RE.match(line)
        if match:
            context.attempt.record_candidate_file(match.group(1), "Search", match.group(2), cwd=repo)
    if len(matching_lines) > data.max_results or len(proc.stdout) > MAX_AGENT_TOOL_RESULT_CHARS:
        context.attempt.mark_truncated_discovery("Search", f"max_results={data.max_results}")
    output = "\n".join(returned_matches)
    suffix = "\n" + "\n".join(record.warnings) if record.warnings else ""
    return output + suffix


def _is_discovery_command(command: str) -> bool:
    return bool(_DISCOVERY_COMMAND_RE.search(command))


def _extract_discovery_candidates(output: str, repo: Path, cwd: Path, context: TaskExecutionContext, source: str) -> None:
    seen: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        options = [line]
        match = _GREP_RESULT_RE.match(line)
        snippet = None
        if match:
            options = [match.group(1)]
            snippet = match.group(2)
        for raw_path in options:
            raw_path = raw_path.strip().strip('"\'').rstrip(':')
            candidate_paths = [Path(raw_path)]
            if not Path(raw_path).is_absolute():
                candidate_paths = [cwd / raw_path, repo / raw_path]
            for candidate_path in candidate_paths:
                try:
                    if not candidate_path.resolve(strict=False).is_file():
                        continue
                except OSError:
                    continue
                record = context.attempt.record_candidate_file(candidate_path, source, snippet)
                if record and record.path not in seen:
                    seen.add(record.path)
                    break
        if len(seen) >= MAX_CANDIDATES_PER_COMMAND:
            context.attempt.mark_truncated_discovery(source, f"candidate extraction capped at {MAX_CANDIDATES_PER_COMMAND}")
            break


def _run_bash(
    data: BashInput,
    repo: Path,
    unsafe: bool,
    debug_callback: Any | None = None,
    tool_call_id: str = "",
    execution_context: TaskExecutionContext | None = None,
) -> tuple[str, dict[str, Any]]:
    lowered = data.command.lower()
    if not unsafe:
        for bad in DENYLIST:
            if bad in lowered:
                raise ValueError(f"Refusing command: {bad.strip()}")
    cwd = _safe_path(repo, data.cwd)
    context = execution_context or TaskExecutionContext(repo)
    if callable(debug_callback):
        debug_callback(
            "command_started",
            {"command": data.command, "cwd": data.cwd, "tool_call_id": tool_call_id},
        )
    proc, record = context.run(data.command, cwd, data.timeout_sec)
    if _is_discovery_command(data.command):
        _extract_discovery_candidates("\n".join((proc.stdout, proc.stderr)), repo, cwd, context, f"Bash: {data.command[:160]}")
        if _PARTIAL_PIPE_RE.search(data.command):
            context.attempt.mark_truncated_discovery("Bash", "discovery pipeline was filtered")
        if len(proc.stdout) > MAX_AGENT_TOOL_RESULT_CHARS or len(proc.stderr) > MAX_AGENT_TOOL_RESULT_CHARS:
            context.attempt.mark_truncated_discovery("Bash", "tool output exceeded runner cap")
    evidence = context.record_validation(
        record,
        kind=data.validation_kind if data.validation_kind in {"project", "smoke"} else "command",
        final_behavior=data.checks_final_behavior,
    )
    compact = compact_command_observation(
        command=data.command,
        record=record,
        stdout=proc.stdout,
        stderr=proc.stderr,
        evidence=evidence,
    )
    full_debug_record = {
        "command": data.command,
        "cwd": data.cwd,
        "exit_code": proc.returncode,
        "timed_out": record.timed_out,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "execution_context": record.to_dict(),
        "validation_evidence": evidence.to_dict(),
        "tool_call_id": tool_call_id,
    }
    if callable(debug_callback):
        debug_callback("command_finished", full_debug_record)
    return json.dumps(compact, ensure_ascii=False), {
        "timed_out": record.timed_out,
        "force_finalization": record.force_finalization,
        "no_progress_warning": record.no_progress_warning,
        "progress_recorded": True,
    }


def _run_write(data: WriteInput, repo: Path, debug_callback: Any | None = None, tool_call_id: str = "", execution_context: TaskExecutionContext | None = None) -> str:
    path = _safe_path(repo, data.file_path)
    existed = path.exists()
    if data.mkdirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data.content, encoding="utf-8")
    if execution_context is not None:
        normalized = execution_context.attempt.normalize_workspace_path(path)
        if normalized:
            (execution_context.attempt.files_modified if existed else execution_context.attempt.files_created).add(normalized)
        execution_context.attempt.mark_candidate_modified(path)
    if callable(debug_callback):
        debug_callback(
            "file_write",
            {
                "file_path": data.file_path,
                "size_bytes": len(data.content.encode("utf-8")),
                "ok": True,
                "tool_call_id": tool_call_id,
            },
        )
    return f"Wrote {path}"


def _run_patch(data: PatchInput, repo: Path, debug_callback: Any | None = None, tool_call_id: str = "", execution_context: TaskExecutionContext | None = None) -> str:
    if data.file_path:
        _safe_path(repo, data.file_path)
    requested_paths: list[str] = []
    hunks_attempted = 0
    try:
        parsed_patches = parse_unified_diff(data.unified_diff)
        hunks_attempted = sum(len(file_patch.hunks) for file_patch in parsed_patches)
        requested_paths = extract_unified_diff_targets(data.unified_diff, default_file_path=data.file_path or None)
    except PatchApplyError:
        if data.file_path:
            requested_paths = [data.file_path]
    try:
        touched, diagnostics = apply_unified_diff_with_diagnostics(
            repo, data.unified_diff, default_file_path=data.file_path or None
        )
    except PatchApplyError as exc:
        if callable(debug_callback):
            target_paths = requested_paths or ([data.file_path] if data.file_path else [""])
            for file_path in target_paths:
                debug_callback(
                    "patch_applied",
                    {
                        "file_path": file_path,
                        "ok": False,
                        "failure_reason": str(exc),
                        "hunks_attempted": hunks_attempted or None,
                        "hunks_failed": hunks_attempted or None,
                        "tool_call_id": tool_call_id,
                    },
                )
        raise ValueError(str(exc)) from exc
    if execution_context is not None:
        for file_path in touched:
            normalized = execution_context.attempt.normalize_workspace_path(file_path)
            if normalized:
                execution_context.attempt.files_modified.add(normalized)
            execution_context.attempt.mark_candidate_modified(file_path)
    if callable(debug_callback):
        for file_path in touched:
            debug_callback(
                "patch_applied",
                {
                    "file_path": file_path,
                    "ok": True,
                    "used_fallback": file_path in diagnostics.fallback_files,
                    "tool_call_id": tool_call_id,
                },
            )
    if diagnostics.fallback_files:
        return (
            f"Patch applied to {len(touched)} file(s); "
            f"whitespace-insensitive fallback used for {len(diagnostics.fallback_files)} file(s)"
        )
    return f"Patch applied to {len(touched)} file(s)"


def _run_webfetch(data: WebFetchInput) -> str:
    u = urlparse(data.url)
    if u.scheme not in {"http", "https"}:
        raise ValueError("Unsupported URL scheme")
    r = httpx.get(data.url, timeout=data.timeout_sec)
    return r.text[:10000]


def _run_git(
    name: str,
    data: GitSimpleInput,
    repo: Path,
    execution_context: TaskExecutionContext | None = None,
) -> str:
    mapping = {
        "GitStatus": ["status", "--short"],
        "GitDiff": ["diff"],
        "GitLog": ["log", "--oneline", "-20"],
        "GitBranch": ["branch"],
        "GitCheckout": ["checkout"],
        "GitCommit": ["commit"],
    }
    cmd = ["git", *mapping[name], *data.args]
    context = execution_context or TaskExecutionContext(repo)
    proc, record = context.run(shlex.join(cmd), repo, 30)
    output = proc.stdout or proc.stderr
    if record.warnings:
        output = output + ("\n" if output else "") + "\n".join(record.warnings)
    return output
