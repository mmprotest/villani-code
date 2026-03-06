from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


VILLANI_DIR = ".villani"


@dataclass(slots=True)
class RepoMap:
    languages: list[str] = field(default_factory=list)
    source_roots: list[str] = field(default_factory=list)
    test_roots: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    likely_entrypoints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectRules:
    rules: list[str]

    def to_markdown(self) -> str:
        if not self.rules:
            return "# Project Rules\n\n- Keep edits scoped and validated."
        lines = ["# Project Rules", ""]
        lines.extend(f"- {r}" for r in self.rules)
        return "\n".join(lines)


@dataclass(slots=True)
class ValidationStep:
    name: str
    command: str
    kind: str
    cost_level: int
    is_mutating: bool
    enabled: bool = True
    scope_hint: str = "project"


@dataclass(slots=True)
class ValidationConfig:
    steps: list[ValidationStep]

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [asdict(s) for s in self.steps]}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ValidationConfig":
        steps: list[ValidationStep] = []
        for row in payload.get("steps", []):
            if not isinstance(row, dict):
                continue
            steps.append(
                ValidationStep(
                    name=str(row.get("name", "")),
                    command=str(row.get("command", "")),
                    kind=str(row.get("kind", "test")),
                    cost_level=int(row.get("cost_level", 5)),
                    is_mutating=bool(row.get("is_mutating", False)),
                    enabled=bool(row.get("enabled", True)),
                    scope_hint=str(row.get("scope_hint", "project")),
                )
            )
        return cls(steps=steps)


@dataclass(slots=True)
class SessionState:
    current_task_summary: str = ""
    last_approved_plan_summary: str = ""
    affected_files: list[str] = field(default_factory=list)
    validation_summary: str = ""
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PY_DETECT = ["pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py"]
JS_DETECT = ["package.json", "tsconfig.json"]
GENERAL_CONFIG = ["Makefile", "justfile", "Dockerfile", "tox.ini", "pytest.ini", "ruff.toml", ".ruff.toml", "mypy.ini", "setup.cfg"]


def _existing(repo: Path, names: list[str]) -> list[str]:
    return sorted([n for n in names if (repo / n).exists()])


def scan_repo(repo: Path) -> tuple[RepoMap, ValidationConfig, ProjectRules]:
    languages: list[str] = []
    manifests: list[str] = []
    if any((repo / n).exists() for n in PY_DETECT):
        languages.append("python")
        manifests.extend(_existing(repo, PY_DETECT))
    if any((repo / n).exists() for n in JS_DETECT):
        languages.append("javascript")
        manifests.extend(_existing(repo, JS_DETECT))

    source_roots = [p for p in ["src", "villani_code", "ui", "lib"] if (repo / p).exists()]
    test_roots = [p for p in ["tests", "test", "__tests__"] if (repo / p).exists()]
    config_files = _existing(repo, GENERAL_CONFIG)
    entrypoints = []
    if (repo / "villani_code" / "cli.py").exists():
        entrypoints.append("villani_code/cli.py")

    repo_map = RepoMap(
        languages=languages,
        source_roots=source_roots,
        test_roots=test_roots,
        manifests=sorted(dict.fromkeys(manifests)),
        config_files=config_files,
        likely_entrypoints=entrypoints,
    )

    steps: list[ValidationStep] = []
    if "python" in languages:
        if (repo / "ruff.toml").exists() or (repo / ".ruff.toml").exists() or (repo / "pyproject.toml").exists():
            steps.append(ValidationStep("ruff-check", "python -m ruff check .", "lint", 1, False))
            steps.append(ValidationStep("ruff-format-check", "python -m ruff format --check .", "format", 1, False))
        if (repo / "mypy.ini").exists() or (repo / "pyproject.toml").exists():
            steps.append(ValidationStep("mypy", "python -m mypy villani_code", "typecheck", 2, False, scope_hint="python_package"))
        if test_roots:
            steps.append(ValidationStep("pytest-targeted", "python -m pytest -q", "test", 2, False, scope_hint="targeted"))
            steps.append(ValidationStep("pytest", "python -m pytest", "test", 4, False))
    if "javascript" in languages and (repo / "package.json").exists():
        steps.append(ValidationStep("npm-lint", "npm run lint --if-present", "lint", 2, False))
        steps.append(ValidationStep("npm-test", "npm test --if-present", "test", 4, False))

    if (repo / "Makefile").exists():
        steps.append(ValidationStep("make-test", "make test", "test", 4, False))

    if not steps:
        steps.append(ValidationStep("git-diff", "git diff --stat", "inspection", 1, False))
    cfg = ValidationConfig(steps=steps)

    rules: list[str] = []
    if test_roots:
        rules.append("Run targeted tests for changed modules before full suites.")
    if "villani_code" in source_roots:
        rules.append("Keep core orchestration logic in focused modules under villani_code/.")
    if "python" in languages:
        rules.append("Prefer typed dataclasses and explicit state over unstructured dicts.")
    rules.append("Keep persistent .villani files compact and deterministic.")
    return repo_map, cfg, ProjectRules(rules=rules[:8])


def init_project_memory(repo: Path) -> dict[str, Path]:
    root = repo / VILLANI_DIR
    root.mkdir(parents=True, exist_ok=True)

    repo_map, validation, rules = scan_repo(repo)
    files = {
        "project_rules": root / "project_rules.md",
        "validation": root / "validation.json",
        "repo_map": root / "repo_map.json",
        "session_state": root / "session_state.json",
    }

    files["project_rules"].write_text(rules.to_markdown() + "\n", encoding="utf-8")
    files["validation"].write_text(json.dumps(validation.to_dict(), indent=2) + "\n", encoding="utf-8")
    files["repo_map"].write_text(json.dumps(repo_map.to_dict(), indent=2) + "\n", encoding="utf-8")
    files["session_state"].write_text(json.dumps(SessionState().to_dict(), indent=2) + "\n", encoding="utf-8")
    return files


def ensure_project_memory(repo: Path) -> dict[str, Path]:
    root = repo / VILLANI_DIR
    required = [
        root / "project_rules.md",
        root / "validation.json",
        root / "repo_map.json",
        root / "session_state.json",
    ]
    if any(not p.exists() for p in required):
        return init_project_memory(repo)
    return {
        "project_rules": required[0],
        "validation": required[1],
        "repo_map": required[2],
        "session_state": required[3],
    }


def load_repo_map(repo: Path) -> dict[str, Any]:
    path = repo / VILLANI_DIR / "repo_map.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_validation_config(repo: Path) -> ValidationConfig:
    path = repo / VILLANI_DIR / "validation.json"
    if not path.exists():
        return ValidationConfig(steps=[])
    return ValidationConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def update_session_state(repo: Path, state: SessionState) -> None:
    path = repo / VILLANI_DIR / "session_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
