from __future__ import annotations

import glob
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict


class LsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = "."
    ignore: list[str] = [".git", ".venv", "__pycache__"]


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str
    max_bytes: int = 40000
    offset_lines: int = 0
    limit_lines: int = 2000


class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str
    path: str = "."
    include_hidden: bool = False
    max_results: int = 100
    head_limit: int = 200


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
    file_path: str
    unified_diff: str


class WebFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_sec: int = 20


class GitSimpleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    args: list[str] = []


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
}

DENYLIST = ["rm -rf", "del /s", "format ", "mkfs", "dd if=", "curl ", "wget "]

SCHEMA_ALLOWED_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "description",
    "default",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "anyOf",
    "oneOf",
    "allOf",
}


def _error(message: str) -> dict[str, Any]:
    return {"content": message, "is_error": True}


def _ok(content: str) -> dict[str, Any]:
    return {"content": content, "is_error": False}


def tool_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    names = list(TOOL_MODELS.items())
    for i, (name, model) in enumerate(names):
        raw = model.model_json_schema()
        cleaned = sanitize_json_schema(raw)
        spec: dict[str, Any] = {
            "name": name,
            "description": f"{name} tool for Villani Code.",
            "input_schema": cleaned,
        }
        if i == len(names) - 1:
            spec["cache_control"] = {"type": "ephemeral"}
        specs.append(
            spec
        )
    return specs


def sanitize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})

    def _resolve_ref(ref: str) -> dict[str, Any]:
        prefix = "#/$defs/"
        if ref.startswith(prefix):
            return defs.get(ref[len(prefix) :], {})
        return {}

    def _walk(node: Any) -> Any:
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            resolved = _resolve_ref(str(node.get("$ref")))
            merged = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
            node = merged

        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"$schema", "$defs", "title", "examples", "$ref"}:
                continue
            if key not in SCHEMA_ALLOWED_KEYS:
                continue
            cleaned[key] = _walk(value)

        if cleaned.get("type") == "object":
            cleaned.setdefault("properties", {})
            cleaned["additionalProperties"] = False
            if "required" not in cleaned:
                cleaned["required"] = []

        return cleaned

    result = _walk(schema)
    if isinstance(result, dict) and result.get("type") == "object":
        result.setdefault("required", [])
        result["additionalProperties"] = False
    return result if isinstance(result, dict) else {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


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
        if name == "Glob":
            return _ok(_run_glob(parsed, repo))
        if name == "Search":
            return _ok(_run_search(parsed, repo))
        if name == "Bash":
            return _ok(_run_bash(parsed, repo, unsafe=unsafe))
        if name == "Write":
            return _ok(_run_write(parsed, repo))
        if name == "Patch":
            return _ok(_run_patch(parsed, repo))
        if name == "WebFetch":
            return _ok(_run_webfetch(parsed))
        if name.startswith("Git"):
            return _ok(_run_git(name, parsed, repo))
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
    lines = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if entry.name in data.ignore:
            continue
        lines.append(f"{entry.name}{'/' if entry.is_dir() else ''}")
    return "\n".join(lines)


def _run_read(data: ReadInput, repo: Path) -> str:
    path = _safe_path(repo, data.file_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(0, data.offset_lines)
    end = start + max(1, data.limit_lines)
    window = lines[start:end]
    numbered = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(window))
    return _truncate_tool_output(numbered, data.max_bytes, "Use offset_lines and limit_lines to read the next window.")


def _run_grep(data: GrepInput, repo: Path) -> str:
    base = _safe_path(repo, data.path)
    rg_bin = shutil.which("rg")
    if rg_bin:
        cmd = [rg_bin, "-n", data.pattern, str(base)]
        if data.include_hidden:
            cmd.append("--hidden")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        lines = proc.stdout.splitlines()[: data.max_results]
        joined = "\n".join(lines)
        if data.head_limit > 0:
            return _truncate_tool_output(joined, data.head_limit, "Reduce scope or increase head_limit for more grep output.")
        return joined
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


def _run_bash(data: BashInput, repo: Path, unsafe: bool) -> str:
    lowered = data.command.lower()
    if not unsafe:
        for bad in DENYLIST:
            if bad in lowered:
                raise ValueError(f"Refusing command: {bad.strip()}")
    cwd = _safe_path(repo, data.cwd)
    proc = subprocess.run(data.command, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=data.timeout_sec)
    return json.dumps({"command": data.command, "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}, indent=2)


def _run_write(data: WriteInput, repo: Path) -> str:
    path = _safe_path(repo, data.file_path)
    if data.mkdirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data.content, encoding="utf-8")
    return f"Wrote {path}"


def _run_patch(data: PatchInput, repo: Path) -> str:
    path = _safe_path(repo, data.file_path)
    proc = subprocess.run(["patch", str(path), "--forward", "--reject-file=-"], input=data.unified_diff, text=True, capture_output=True, cwd=str(repo))
    if proc.returncode != 0:
        raise ValueError(proc.stderr or proc.stdout)
    return proc.stdout.strip() or "Patch applied"


def _run_webfetch(data: WebFetchInput) -> str:
    u = urlparse(data.url)
    if u.scheme not in {"http", "https"}:
        raise ValueError("Unsupported URL scheme")
    r = httpx.get(data.url, timeout=data.timeout_sec)
    return r.text[:10000]


def _run_git(name: str, data: GitSimpleInput, repo: Path) -> str:
    mapping = {
        "GitStatus": ["status", "--short"],
        "GitDiff": ["diff", "--unified=1"],
        "GitLog": ["log", "--oneline", "-20"],
        "GitBranch": ["branch"],
        "GitCheckout": ["checkout"],
        "GitCommit": ["commit"],
    }
    cmd = ["git", *mapping[name], *data.args]
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    return proc.stdout or proc.stderr


def _truncate_tool_output(text: str, max_bytes: int, hint: str) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    cutoff = max(0, max_bytes)
    truncated = raw[:cutoff].decode("utf-8", errors="ignore")
    return f"{truncated}\n...[truncated] {hint}"
