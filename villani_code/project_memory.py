from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


VILLANI_DIR = ".villani"

LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx"),
    "typescript": (".ts", ".tsx"),
    "rust": (".rs",),
    "go": (".go",),
}

MANIFEST_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
}

CONFIG_HINT_FILES = {
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    "tox.ini",
    "pytest.ini",
    ".flake8",
    "tsconfig.json",
    "eslint.config.js",
    ".eslintrc",
    ".eslintrc.json",
    "Makefile",
    "justfile",
}

ENTRYPOINT_HINTS = {
    "main.py",
    "app.py",
    "manage.py",
    "villani_code/cli.py",
    "src/main.py",
    "src/index.ts",
}


@dataclass(slots=True)
class RepoMap:
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_roots: list[str] = field(default_factory=list)
    source_roots: list[str] = field(default_factory=list)
    test_roots: list[str] = field(default_factory=list)
    docs_roots: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    likely_entrypoints: list[str] = field(default_factory=list)
    validation_toolchain_hints: list[str] = field(default_factory=list)
    repo_shape: str = "monolithic"
    module_relationships: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProjectRules:
    rules: list[str]

    def to_markdown(self) -> str:
        lines = ["# Project Rules", ""]
        if not self.rules:
            lines.append("- Keep edits scoped and validated.")
        else:
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
    language_family: str = ""
    target_strategy: str = "none"


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
                    language_family=str(row.get("language_family", "")),
                    target_strategy=str(row.get("target_strategy", "none")),
                )
            )
        return cls(steps=steps)


@dataclass(slots=True)
class SessionState:
    task_summary: str = ""
    plan_summary: str = ""
    plan_risk: str = ""
    action_classes: list[str] = field(default_factory=list)
    estimated_scope: str = ""
    affected_files: list[str] = field(default_factory=list)
    validation_summary: str = ""
    last_failed_step: str = ""
    repair_attempt_summaries: list[dict[str, Any]] = field(default_factory=list)
    outcome_status: str = "pending"
    checkpoint_note: str = ""

    # compatibility fields
    current_task_summary: str = ""
    last_approved_plan_summary: str = ""
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["task_summary"]:
            payload["task_summary"] = payload["current_task_summary"]
        if not payload["plan_summary"]:
            payload["plan_summary"] = payload["last_approved_plan_summary"]
        if not payload["repair_attempt_summaries"] and payload["repair_attempts"]:
            payload["repair_attempt_summaries"] = payload["repair_attempts"]
        return payload


@dataclass(slots=True)
class RepoDiscovery:
    directories: list[str]
    files: list[str]
    language_counts: dict[str, int]


def _iter_repo_paths(repo: Path) -> RepoDiscovery:
    dirs: set[str] = set()
    files: list[str] = []
    language_counts = {k: 0 for k in LANGUAGE_EXTENSIONS}
    for path in repo.rglob("*"):
        if any(part.startswith(".") and part not in {".github"} for part in path.parts if part not in {"."}):
            if ".villani" in path.parts:
                continue
            if ".git" in path.parts:
                continue
        if path.is_dir():
            rel = path.relative_to(repo).as_posix()
            if rel:
                dirs.add(rel)
            continue
        rel = path.relative_to(repo).as_posix()
        if rel.startswith(".villani/"):
            continue
        files.append(rel)
        suffix = path.suffix.lower()
        for lang, exts in LANGUAGE_EXTENSIONS.items():
            if suffix in exts:
                language_counts[lang] += 1
    return RepoDiscovery(directories=sorted(dirs), files=sorted(files), language_counts=language_counts)


def _infer_roots(paths: list[str], predicate: Any) -> list[str]:
    roots = sorted({p for p in paths if predicate(p)})
    return roots[:12]


def _infer_languages(discovery: RepoDiscovery, manifests: list[str]) -> list[str]:
    langs = {lang for lang, count in discovery.language_counts.items() if count > 0}
    for manifest in manifests:
        if manifest.startswith("pyproject") or manifest.startswith("requirements") or manifest.startswith("setup"):
            langs.add("python")
        if manifest == "package.json":
            langs.update({"javascript", "typescript"})
        if manifest == "Cargo.toml":
            langs.add("rust")
        if manifest == "go.mod":
            langs.add("go")
    return sorted(langs)


def _infer_frameworks(files: list[str], manifests: list[str]) -> list[str]:
    frameworks: set[str] = set()
    text = " ".join(files + manifests).lower()
    if "pytest" in text or "pytest.ini" in text:
        frameworks.add("pytest")
    if "mypy" in text:
        frameworks.add("mypy")
    if "ruff" in text:
        frameworks.add("ruff")
    if "django" in text or "manage.py" in files:
        frameworks.add("django")
    return sorted(frameworks)


def _build_validation_steps(repo_map: RepoMap) -> list[ValidationStep]:
    steps: list[ValidationStep] = []
    manifests = set(repo_map.manifests)
    configs = set(repo_map.config_files)
    has_python = "python" in repo_map.languages

    if has_python:
        if {"ruff.toml", ".ruff.toml", "pyproject.toml"} & (configs | manifests):
            steps.append(ValidationStep("ruff-format-check", "python -m ruff format --check .", "format", 1, False, scope_hint="repo", language_family="python", target_strategy="changed_files"))
            steps.append(ValidationStep("ruff-check", "python -m ruff check .", "lint", 1, False, scope_hint="repo", language_family="python", target_strategy="changed_files"))
        if {"mypy.ini", "pyproject.toml"} & (configs | manifests):
            target = repo_map.package_roots[0] if repo_map.package_roots else "."
            steps.append(ValidationStep("mypy", f"python -m mypy {target}", "typecheck", 2, False, scope_hint="package", language_family="python", target_strategy="package"))
        if repo_map.test_roots:
            steps.append(ValidationStep("pytest-targeted", "python -m pytest -q", "test", 2, False, scope_hint="targeted", language_family="python", target_strategy="related_tests"))
            steps.append(ValidationStep("pytest", "python -m pytest", "test", 4, False, scope_hint="repo", language_family="python", target_strategy="full"))

    if "typescript" in repo_map.languages or "javascript" in repo_map.languages:
        if "package.json" in manifests:
            steps.append(ValidationStep("npm-lint", "npm run lint --if-present", "lint", 2, False, scope_hint="repo", language_family="node", target_strategy="full"))
            steps.append(ValidationStep("npm-test", "npm test --if-present", "test", 4, False, scope_hint="repo", language_family="node", target_strategy="full"))

    if not steps:
        steps.append(ValidationStep("git-diff", "git diff --stat", "inspection", 1, False, scope_hint="repo", language_family="generic", target_strategy="none"))

    return sorted(steps, key=lambda s: (s.cost_level, s.kind, s.name))


def _shape_from_roots(source_roots: list[str]) -> str:
    if len(source_roots) >= 4:
        return "multi_root"
    if any(root.startswith("packages/") for root in source_roots):
        return "package_based"
    return "monolithic"


def _module_relationships(repo_map: RepoMap) -> list[str]:
    relations: list[str] = []
    for test_root in repo_map.test_roots:
        for src_root in repo_map.source_roots:
            if src_root != test_root:
                relations.append(f"{test_root} validates {src_root}")
    return sorted(dict.fromkeys(relations))[:8]


def scan_repo(repo: Path) -> tuple[RepoMap, ValidationConfig, ProjectRules]:
    discovery = _iter_repo_paths(repo)
    manifests = sorted([p for p in discovery.files if Path(p).name in MANIFEST_FILES])
    config_files = sorted([p for p in discovery.files if Path(p).name in CONFIG_HINT_FILES])
    languages = _infer_languages(discovery, [Path(m).name for m in manifests])

    source_roots = _infer_roots(discovery.directories, lambda p: p.endswith("src") or p.startswith("src/") or any((repo / p).glob("*.py")) and not p.startswith("tests"))
    if not source_roots:
        source_roots = sorted({Path(f).parts[0] for f in discovery.files if "/" in f and not f.startswith("tests/")})[:8]

    test_roots = _infer_roots(discovery.directories, lambda p: p == "tests" or p.startswith("tests/") or p.endswith("__tests__"))
    docs_roots = _infer_roots(discovery.directories, lambda p: p == "docs" or p.startswith("docs/"))
    package_roots = sorted({root for root in source_roots if (repo / root / "__init__.py").exists() or (repo / root).name == "src"})[:8]
    entrypoints = sorted([p for p in ENTRYPOINT_HINTS if (repo / p).exists()])

    repo_map = RepoMap(
        languages=languages,
        frameworks=_infer_frameworks(discovery.files, manifests + config_files),
        package_roots=package_roots,
        source_roots=source_roots,
        test_roots=test_roots,
        docs_roots=docs_roots,
        manifests=manifests,
        config_files=config_files,
        likely_entrypoints=entrypoints,
        validation_toolchain_hints=[],
        repo_shape=_shape_from_roots(source_roots),
        module_relationships=[],
        summary={
            "file_count": len(discovery.files),
            "directory_count": len(discovery.directories),
            "top_level_entries": sorted({Path(p).parts[0] for p in discovery.files if p})[:16],
        },
    )
    repo_map.module_relationships = _module_relationships(repo_map)
    repo_map.validation_toolchain_hints = sorted({s.name for s in _build_validation_steps(repo_map)})

    validation = ValidationConfig(_build_validation_steps(repo_map))
    rules: list[str] = []
    if "python" in repo_map.languages:
        rules.append("Use ruff/mypy/pytest steps from .villani/validation.json before finalizing edits.")
    if repo_map.test_roots:
        rules.append(f"Prefer targeted tests in {repo_map.test_roots[0]} before broad suites.")
    if repo_map.source_roots and any(root == "src" or root.startswith("src/") for root in repo_map.source_roots):
        rules.append("Project uses src-style layout; keep imports and tests aligned with src/ modules.")
    if repo_map.repo_shape == "multi_root":
        rules.append("Repository is multi-root; keep changes scoped to a single root when possible.")
    rules.append("Keep .villani memory files compact and deterministic.")

    return repo_map, validation, ProjectRules(rules=rules[:8])


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
    files["validation"].write_text(json.dumps(validation.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files["repo_map"].write_text(json.dumps(repo_map.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files["session_state"].write_text(json.dumps(SessionState().to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
