from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class LsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = "."
    ignore: list[str] = [".git", ".venv", "__pycache__"]


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
    file_path: str
    unified_diff: str


TOOL_MODELS: dict[str, type[BaseModel]] = {
    "Ls": LsInput,
    "Read": ReadInput,
    "Grep": GrepInput,
    "Bash": BashInput,
    "Write": WriteInput,
    "Patch": PatchInput,
}

DENYLIST = ["rm -rf", "del /s", "format ", "mkfs", "dd if="]


def _error(message: str) -> dict[str, Any]:
    return {"content": message, "is_error": True}


def _ok(content: str) -> dict[str, Any]:
    return {"content": content, "is_error": False}


def tool_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    descriptions = {
        "Ls": "List files in a path while honoring ignore names.",
        "Read": "Read a text file with byte limit.",
        "Grep": "Search for a regex-like pattern in files.",
        "Bash": "Run a shell command with timeout and safety guardrails.",
        "Write": "Write text content to a file.",
        "Patch": "Apply a unified diff patch to a target file.",
    }
    for name, model in TOOL_MODELS.items():
        specs.append(
            {
                "name": name,
                "description": descriptions[name],
                "input_schema": model.model_json_schema(),
            }
        )
    return specs


def execute_tool(name: str, raw_input: dict[str, Any], repo: Path, unsafe: bool = False) -> dict[str, Any]:
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
            return _ok(_run_read(parsed, repo))
        if name == "Grep":
            return _ok(_run_grep(parsed, repo))
        if name == "Bash":
            return _ok(_run_bash(parsed, repo, unsafe=unsafe))
        if name == "Write":
            return _ok(_run_write(parsed, repo))
        if name == "Patch":
            return _ok(_run_patch(parsed, repo))
    except Exception as exc:
        return _error(str(exc))
    return _error("Unhandled tool")


def _safe_path(repo: Path, raw: str) -> Path:
    path = (repo / raw).resolve()
    repo_resolved = repo.resolve()
    if not str(path).startswith(str(repo_resolved)):
        raise ValueError("Path escapes repository")
    return path


def _run_ls(data: LsInput, repo: Path) -> str:
    target = _safe_path(repo, data.path)
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {data.path}")
    lines: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if entry.name in data.ignore:
            continue
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"{entry.name}{suffix}")
    return "\n".join(lines)


def _run_read(data: ReadInput, repo: Path) -> str:
    path = _safe_path(repo, data.file_path)
    raw = path.read_bytes()
    truncated = len(raw) > data.max_bytes
    raw = raw[: data.max_bytes]
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        text += "\n...[truncated]"
    return text


def _run_grep(data: GrepInput, repo: Path) -> str:
    base = _safe_path(repo, data.path)
    matches: list[str] = []
    rg_bin = shutil.which("rg")
    if rg_bin:
        cmd = [rg_bin, "-n", data.pattern, str(base)]
        if data.include_hidden:
            cmd.append("--hidden")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        output = proc.stdout.splitlines()
        matches = output[: data.max_results]
        return "\n".join(matches)

    for file in base.rglob("*"):
        if not file.is_file():
            continue
        if not data.include_hidden and any(part.startswith(".") for part in file.parts):
            continue
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines, start=1):
            if data.pattern in line:
                matches.append(f"{file}:{idx}:{line}")
                if len(matches) >= data.max_results:
                    return "\n".join(matches)
    return "\n".join(matches)


def _run_bash(data: BashInput, repo: Path, unsafe: bool) -> str:
    lowered = data.command.lower()
    if not unsafe:
        for bad in DENYLIST:
            if bad in lowered:
                raise ValueError(f"Refusing potentially destructive command: {bad}")
    cwd = _safe_path(repo, data.cwd)
    proc = subprocess.run(
        data.command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=data.timeout_sec,
    )
    payload = {
        "command": data.command,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    return json.dumps(payload, indent=2)


def _run_write(data: WriteInput, repo: Path) -> str:
    path = _safe_path(repo, data.file_path)
    if data.mkdirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data.content, encoding="utf-8")
    return f"Wrote {len(data.content)} bytes to {path}"


def _run_patch(data: PatchInput, repo: Path) -> str:
    path = _safe_path(repo, data.file_path)
    proc = subprocess.run(
        ["patch", str(path), "--forward", "--reject-file=-"],
        input=data.unified_diff,
        text=True,
        capture_output=True,
        cwd=str(repo),
    )
    if proc.returncode != 0:
        raise ValueError(f"Patch failed: {proc.stderr or proc.stdout}")
    return proc.stdout.strip() or "Patch applied"
