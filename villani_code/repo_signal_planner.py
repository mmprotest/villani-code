from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from villani_code.path_authority import (
    is_internal_villani_path,
    should_ignore_for_greenfield_context,
)


def classify_path_authority(path: str) -> str:
    rel = str(path)
    if should_ignore_for_greenfield_context(rel):
        if is_internal_villani_path(rel):
            return "internal_artifact_ignored"
        return "internal_artifact_low_authority"
    if rel.startswith(("src/", "lib/", "app/", "tests/", "test/")) or Path(rel).name in {
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
    }:
        return "user_workspace_authoritative"
    return "user_workspace_supporting"


def collect_repo_signals(repo_root: str) -> dict[str, Any]:
    repo = Path(repo_root)
    files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    rel = [p.relative_to(repo).as_posix() for p in files]
    path_authority = {x: classify_path_authority(x) for x in rel}
    non_internal = [x for x in rel if path_authority.get(x) not in {"internal_artifact_ignored", "internal_artifact_low_authority"}]

    source_roots = sorted({x.split("/", 1)[0] for x in non_internal if x.startswith(("src/", "lib/", "app/", "villani_code/"))})
    test_roots = sorted({x.split("/", 1)[0] for x in non_internal if x.startswith(("tests/", "test/")) or "/tests/" in x})
    config_files = sorted([x for x in non_internal if Path(x).name in {"pyproject.toml", "package.json", "Makefile", "tox.ini", "setup.cfg", "setup.py", "requirements.txt", "ruff.toml", "mypy.ini"}])
    docs = [x for x in non_internal if x.lower().startswith("docs/") or Path(x).name.lower().startswith("readme")]
    hint_files = [
        x
        for x in non_internal
        if Path(x).name.lower().startswith(("readme", "notes", "constraints"))
        or x.lower().endswith((".csv", ".tsv", ".json", ".yaml", ".yml", ".txt"))
    ]
    code_files = [x for x in non_internal if Path(x).suffix.lower() in {".py", ".js", ".ts", ".go", ".rs", ".java", ".rb"}]
    entrypoint_like_files = [
        x
        for x in code_files
        if Path(x).name in {"main.py", "app.py", "run.py", "__main__.py", "index.js", "main.ts"}
        or x.startswith(("src/main.", "app/main.", "bin/"))
    ]

    tooling_hints: list[str] = []
    likely_validation: list[str] = []
    maintenance_checks: list[str] = []
    if "pyproject.toml" in non_internal or any(x.endswith(".py") for x in non_internal):
        tooling_hints.append("python")
        likely_validation.extend(["pytest -q", "python -m pytest -q"])
        maintenance_checks.extend(["python -m compileall -q ."])
    if "package.json" in non_internal:
        tooling_hints.append("node")
        likely_validation.extend(["npm test", "npm run test"])
    if "go.mod" in non_internal:
        tooling_hints.append("go")
        likely_validation.append("go test ./...")
    if "Cargo.toml" in non_internal:
        tooling_hints.append("rust")
        likely_validation.append("cargo test")
    if "Makefile" in non_internal:
        likely_validation.extend(["make test", "make check"])

    if "pyproject.toml" in non_internal:
        maintenance_checks.extend(["python -m pip check"])
    if any(Path(x).name == "__init__.py" for x in non_internal):
        maintenance_checks.append("python -c 'import villani_code' || true")

    likely_validation = list(dict.fromkeys([c.strip() for c in likely_validation if c.strip()]))
    maintenance_checks = list(dict.fromkeys([c.strip() for c in maintenance_checks if c.strip()]))

    return {
        "likely_source_roots": source_roots,
        "likely_test_roots": test_roots,
        "docs_present": bool(docs),
        "docs_files": docs[:20],
        "config_files": config_files,
        "tooling_hints": tooling_hints,
        "likely_validation_commands": likely_validation[:8],
        "maintenance_commands": maintenance_checks[:8],
        "git_available": shutil.which("git") is not None and (repo / ".git").exists(),
        "language_hints": tooling_hints,
        "repo_size_files": len(rel),
        "non_internal_file_count": len(non_internal),
        "workspace_empty_or_internal_only": len(non_internal) == 0,
        "workspace_lightweight_hints_only": len(non_internal) > 0 and not code_files and bool(hint_files),
        "workspace_hint_files": hint_files[:30],
        "entrypoint_like_files": entrypoint_like_files[:20],
        "sample_data_files": [x for x in hint_files if x.lower().endswith((".csv", ".tsv", ".json"))][:20],
        "existing_project_detected": bool(code_files or config_files or source_roots),
        "workspace_sparse_greenfield_like": bool(
            len(non_internal) <= 8
            and (
                not bool(code_files or config_files or source_roots)
                or (len(code_files) <= 3 and not test_roots and not entrypoint_like_files)
            )
        ),
        "likely_project_directions": _likely_project_directions(tooling_hints, hint_files, non_internal),
        "internal_artifact_paths": [x for x in rel if is_internal_villani_path(x)],
        "ignored_context_paths": [x for x in rel if path_authority.get(x) in {"internal_artifact_ignored", "internal_artifact_low_authority"}],
        "path_authority": path_authority,
    }


def _likely_project_directions(tooling_hints: list[str], hint_files: list[str], non_internal: list[str]) -> list[str]:
    directions: list[str] = []
    if any(x.lower().endswith((".csv", ".tsv")) for x in hint_files):
        directions.append("data_quality_checker")
        directions.append("csv_analysis_cli")
    if "python" in tooling_hints:
        directions.append("python_cli_utility")
    if any(Path(x).name.lower().startswith("readme") for x in hint_files):
        directions.append("local_automation_tool")
    if not non_internal:
        directions.extend(["python_cli_utility", "file_report_generator"])
    return list(dict.fromkeys(directions))[:6]
