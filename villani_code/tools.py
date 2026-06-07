from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

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


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    path: str = "."
    context_lines: int = 2


class BashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    cwd: str = "."
    timeout_sec: int = 30


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


def _error(message: str) -> dict[str, Any]:
    return {"content": message, "is_error": True}


def _ok(content: str) -> dict[str, Any]:
    return {"content": content, "is_error": False}


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
            return _ok(_run_read(parsed, repo, debug_callback=debug_callback, tool_call_id=tool_call_id))
        if name == "Grep":
            return _ok(_run_grep(parsed, repo))
        if name == "Glob":
            return _ok(_run_glob(parsed, repo))
        if name == "Search":
            return _ok(_run_search(parsed, repo))
        if name == "Bash":
            return _ok(_run_bash(parsed, repo, unsafe=unsafe, debug_callback=debug_callback, tool_call_id=tool_call_id))
        if name == "Write":
            return _ok(_run_write(parsed, repo, debug_callback=debug_callback, tool_call_id=tool_call_id))
        if name == "Patch":
            return _ok(_run_patch(parsed, repo, debug_callback=debug_callback, tool_call_id=tool_call_id))
        if name == "WebFetch":
            return _ok(_run_webfetch(parsed))
        if name.startswith("Git"):
            return _ok(_run_git(name, parsed, repo))
        if name == "SubmitPlan":
            return _ok("Plan artifact submitted")
    except Exception as exc:
        return _error(str(exc))
    return _error("Unhandled tool")


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


def _run_read(data: ReadInput, repo: Path, debug_callback: Any | None = None, tool_call_id: str = "") -> str:
    path = _safe_path(repo, data.file_path)
    raw = path.read_bytes()[: data.max_bytes]
    if callable(debug_callback):
        debug_callback("file_read", {"file_path": data.file_path, "size_bytes": len(raw), "ok": True, "tool_call_id": tool_call_id})
    return raw.decode("utf-8", errors="replace")


def _run_grep(data: GrepInput, repo: Path) -> str:
    base = _safe_path(repo, data.path)
    rg_bin = shutil.which("rg")
    if rg_bin:
        cmd = [rg_bin, "-n", data.pattern, str(base)]
        if data.include_hidden:
            cmd.append("--hidden")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return "\n".join(proc.stdout.splitlines()[: data.max_results])
    return ""


def _run_glob(data: GlobInput, repo: Path) -> str:
    hits = [str(Path(p).relative_to(repo)) for p in glob.glob(str(repo / data.pattern), recursive=True)]
    return "\n".join(sorted(hits))


def _run_search(data: SearchInput, repo: Path) -> str:
    rg_bin = shutil.which("rg")
    if not rg_bin:
        return _run_grep(GrepInput(pattern=data.query, path=data.path), repo)
    base = _safe_path(repo, data.path)
    cmd = [rg_bin, "-n", "-C", str(data.context_lines), data.query, str(base)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stdout


def _task_command_env() -> dict[str, str]:
    env = os.environ.copy()

    # Keep Villani's private runtime out of model-issued task commands.
    # Villani itself may run inside a venv, but Bash tool commands should
    # execute against the task environment, not the agent runtime.
    candidate_bins: set[str] = set()

    agent_venv = os.environ.get("VIRTUAL_ENV")
    if agent_venv:
        candidate_bins.add(os.path.normpath(os.path.join(agent_venv, "bin")))

    # Villani may be launched from a venv via PATH without VIRTUAL_ENV set.
    # In that case, sys.executable still identifies the private runtime.
    executable_bin = os.path.normpath(os.path.dirname(sys.executable))
    executable_prefix = os.path.normpath(sys.prefix)
    base_prefix = os.path.normpath(getattr(sys, "base_prefix", sys.prefix))

    if executable_prefix != base_prefix or os.path.exists(os.path.join(executable_prefix, "pyvenv.cfg")):
        candidate_bins.add(executable_bin)

    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)

    if candidate_bins:
        env["PATH"] = os.pathsep.join(
            part
            for part in env.get("PATH", "").split(os.pathsep)
            if part and os.path.normpath(part) not in candidate_bins
        )

    return env


def _run_bash(data: BashInput, repo: Path, unsafe: bool, debug_callback: Any | None = None, tool_call_id: str = "") -> str:
    lowered = data.command.lower()
    if not unsafe:
        for bad in DENYLIST:
            if bad in lowered:
                raise ValueError(f"Refusing command: {bad.strip()}")
    cwd = _safe_path(repo, data.cwd)
    if callable(debug_callback):
        debug_callback("command_started", {"command": data.command, "cwd": data.cwd, "tool_call_id": tool_call_id})
    proc = subprocess.run(
        data.command,
        shell=True,
        cwd=str(cwd),
        env=_task_command_env(),
        capture_output=True,
        text=True,
        timeout=data.timeout_sec,
    )
    if callable(debug_callback):
        debug_callback(
            "command_finished",
            {
                "command": data.command,
                "cwd": data.cwd,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "truncated": False,
                "tool_call_id": tool_call_id,
            },
        )
    return json.dumps({"command": data.command, "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}, indent=2)


def _run_write(data: WriteInput, repo: Path, debug_callback: Any | None = None, tool_call_id: str = "") -> str:
    path = _safe_path(repo, data.file_path)
    if data.mkdirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data.content, encoding="utf-8")
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


def _run_patch(data: PatchInput, repo: Path, debug_callback: Any | None = None, tool_call_id: str = "") -> str:
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


def _run_git(name: str, data: GitSimpleInput, repo: Path) -> str:
    mapping = {
        "GitStatus": ["status", "--short"],
        "GitDiff": ["diff"],
        "GitLog": ["log", "--oneline", "-20"],
        "GitBranch": ["branch"],
        "GitCheckout": ["checkout"],
        "GitCommit": ["commit"],
    }
    cmd = ["git", *mapping[name], *data.args]
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    return proc.stdout or proc.stderr
