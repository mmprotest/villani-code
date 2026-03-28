from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def collect_repo_signals(repo_root: str) -> dict[str, Any]:
    repo = Path(repo_root)
    files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    rel = [p.relative_to(repo).as_posix() for p in files]

    source_roots = sorted({x.split("/", 1)[0] for x in rel if x.startswith(("src/", "lib/", "app/", "villani_code/"))})
    test_roots = sorted({x.split("/", 1)[0] for x in rel if x.startswith(("tests/", "test/")) or "/tests/" in x})
    config_files = sorted([x for x in rel if Path(x).name in {"pyproject.toml", "package.json", "Makefile", "tox.ini", "setup.cfg", "setup.py", "requirements.txt", "ruff.toml", "mypy.ini"}])
    docs = [x for x in rel if x.lower().startswith("docs/") or Path(x).name.lower().startswith("readme")]

    tooling_hints: list[str] = []
    likely_validation: list[str] = []
    maintenance_checks: list[str] = []
    if "pyproject.toml" in rel or any(x.endswith(".py") for x in rel):
        tooling_hints.append("python")
        likely_validation.extend(["pytest -q", "python -m pytest -q"])
        maintenance_checks.extend(["python -m compileall -q ."])
    if "package.json" in rel:
        tooling_hints.append("node")
        likely_validation.extend(["npm test", "npm run test"])
    if "go.mod" in rel:
        tooling_hints.append("go")
        likely_validation.append("go test ./...")
    if "Cargo.toml" in rel:
        tooling_hints.append("rust")
        likely_validation.append("cargo test")
    if "Makefile" in rel:
        likely_validation.extend(["make test", "make check"])

    if "pyproject.toml" in rel:
        maintenance_checks.extend(["python -m pip check"])
    if any(Path(x).name == "__init__.py" for x in rel):
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
    }
