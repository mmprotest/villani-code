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
    "poetry.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
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
    ".pre-commit-config.yaml",
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
    package_managers: list[str] = field(default_factory=list)
    package_roots: list[str] = field(default_factory=list)
    source_roots: list[str] = field(default_factory=list)
    test_roots: list[str] = field(default_factory=list)
    docs_roots: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    major_configs: list[str] = field(default_factory=list)
    likely_entrypoints: list[str] = field(default_factory=list)
    build_system_hints: list[str] = field(default_factory=list)
    ci_hints: list[str] = field(default_factory=list)
    generated_code_hints: list[str] = field(default_factory=list)
    package_test_relationships: list[dict[str, str]] = field(default_factory=list)
    source_test_patterns: list[dict[str, str]] = field(default_factory=list)
    validation_toolchain_hints: list[str] = field(default_factory=list)
    repo_shape: str = "single_package"
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
    escalation_role: str = "optional"
    typical_trigger_conditions: list[str] = field(default_factory=list)


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
                    escalation_role=str(row.get("escalation_role", "optional")),
                    typical_trigger_conditions=[str(v) for v in row.get("typical_trigger_conditions", []) if isinstance(v, (str, int, float))],
                )
            )
        return cls(steps=steps)


@dataclass(slots=True)
class SessionState:
    task_summary: str = ""
    plan_summary: str = ""
    plan_risk: str = ""
    grounding_evidence_summary: list[str] = field(default_factory=list)
    action_classes: list[str] = field(default_factory=list)
    estimated_scope: str = ""
    change_impact: str = ""
    task_mode: str = "general"
    candidate_targets_summary: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    validation_plan_summary: list[str] = field(default_factory=list)
    validation_summary: str = ""
    last_failed_step: str = ""
    repair_attempt_summaries: list[dict[str, Any]] = field(default_factory=list)
    outcome_status: str = "pending"
    next_step_hints: list[str] = field(default_factory=list)
    handoff_checkpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionState":
        if not isinstance(payload, dict):
            return cls()

        def _text(key: str) -> str:
            value = payload.get(key, "")
            return value.strip() if isinstance(value, str) else ""

        def _text_list(key: str) -> list[str]:
            value = payload.get(key, [])
            if not isinstance(value, list):
                return []
            items: list[str] = []
            for item in value:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        items.append(text)
            return items

        def _dict_list(key: str) -> list[dict[str, Any]]:
            value = payload.get(key, [])
            if not isinstance(value, list):
                return []
            return [item for item in value if isinstance(item, dict)]

        return cls(
            task_summary=_text("task_summary"),
            plan_summary=_text("plan_summary"),
            plan_risk=_text("plan_risk"),
            grounding_evidence_summary=_text_list("grounding_evidence_summary"),
            action_classes=_text_list("action_classes"),
            estimated_scope=_text("estimated_scope"),
            change_impact=_text("change_impact"),
            task_mode=_text("task_mode") or "general",
            candidate_targets_summary=_text_list("candidate_targets_summary"),
            affected_files=_text_list("affected_files"),
            validation_plan_summary=_text_list("validation_plan_summary"),
            validation_summary=_text("validation_summary"),
            last_failed_step=_text("last_failed_step"),
            repair_attempt_summaries=_dict_list("repair_attempt_summaries"),
            outcome_status=_text("outcome_status") or "pending",
            next_step_hints=_text_list("next_step_hints"),
            handoff_checkpoint=_text("handoff_checkpoint"),
        )


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
        rel = path.relative_to(repo).as_posix()
        if not rel:
            continue
        parts = path.parts
        if any(part in {".git", ".villani", ".villani_code", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"} for part in parts):
            continue
        if path.is_dir():
            dirs.add(rel)
            continue
        files.append(rel)
        suffix = path.suffix.lower()
        for lang, exts in LANGUAGE_EXTENSIONS.items():
            if suffix in exts:
                language_counts[lang] += 1
    return RepoDiscovery(directories=sorted(dirs), files=sorted(files), language_counts=language_counts)


def _infer_languages(discovery: RepoDiscovery, manifests: list[str]) -> list[str]:
    langs = [k for k, v in discovery.language_counts.items() if v > 0]
    names = {Path(m).name for m in manifests}
    if {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"} & names:
        langs.append("python")
    if "package.json" in names:
        langs.extend(["javascript", "typescript"])
    if "Cargo.toml" in names:
        langs.append("rust")
    if "go.mod" in names:
        langs.append("go")
    return sorted(set(langs))


def _infer_frameworks(files: list[str], manifests: list[str], configs: list[str]) -> list[str]:
    text = " ".join(files + manifests + configs).lower()
    frameworks: set[str] = set()
    for marker, name in [
        ("pytest", "pytest"),
        ("mypy", "mypy"),
        ("ruff", "ruff"),
        ("django", "django"),
        ("fastapi", "fastapi"),
        ("flask", "flask"),
        ("vitest", "vitest"),
        ("jest", "jest"),
    ]:
        if marker in text:
            frameworks.add(name)
    return sorted(frameworks)


def _source_roots(repo: Path, discovery: RepoDiscovery) -> list[str]:
    roots: set[str] = set()
    if (repo / "src").exists():
        roots.add("src")
    for file in discovery.files:
        p = Path(file)
        if p.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}:
            top = p.parts[0]
            if top not in {"tests", "test", "docs"}:
                roots.add(top)
    return sorted(roots)[:12]


def _test_roots(discovery: RepoDiscovery) -> list[str]:
    roots: set[str] = set()
    for d in discovery.directories:
        if d == "tests" or d.startswith("tests/") or d.endswith("/__tests__"):
            roots.add(d.split("/")[0] if d.startswith("tests/") else d)
    for file in discovery.files:
        if file.startswith("tests/") or file.endswith("_test.py") or file.endswith(".spec.ts") or file.endswith(".test.ts"):
            roots.add("tests")
    return sorted(roots)[:12]


def _package_test_relationships(source_roots: list[str], test_roots: list[str]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    for src in source_roots:
        test = "tests" if "tests" in test_roots else (test_roots[0] if test_roots else "")
        if test:
            pairs.append({"package_root": src, "test_root": test, "relationship": "nearby_or_mirrored"})
    return pairs[:12]


def _source_test_patterns(source_roots: list[str], test_roots: list[str]) -> list[dict[str, str]]:
    patterns: list[dict[str, str]] = []
    if any(r == "src" for r in source_roots) and test_roots:
        patterns.append({"source_pattern": "src/<module>.py", "test_pattern": "tests/test_<module>.py"})
        patterns.append({"source_pattern": "src/<pkg>/<module>.py", "test_pattern": "tests/<pkg>/test_<module>.py"})
    if any(r != "src" for r in source_roots) and test_roots:
        patterns.append({"source_pattern": "<pkg>/<module>.py", "test_pattern": "tests/test_<module>.py"})
    return patterns[:8]


def _package_managers(manifests: list[str], lockfiles: list[str]) -> list[str]:
    names = {Path(m).name for m in manifests + lockfiles}
    managers: set[str] = set()
    if {"pyproject.toml", "requirements.txt", "poetry.lock"} & names:
        managers.add("python")
    if {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"} & names:
        managers.add("node")
    if "Cargo.toml" in names:
        managers.add("cargo")
    if "go.mod" in names:
        managers.add("go")
    return sorted(managers)


def _repo_shape(source_roots: list[str], manifests: list[str]) -> str:
    top_manifests = {Path(m).parts[0] for m in manifests if "/" in m}
    if any(root.startswith("packages") for root in source_roots) or len(top_manifests) > 1:
        return "multi_root"
    if len(source_roots) > 2:
        return "multi_root"
    return "single_package"


def _build_validation_steps(repo_map: RepoMap) -> list[ValidationStep]:
    steps: list[ValidationStep] = []
    has_python = "python" in repo_map.languages
    if has_python:
        steps.append(ValidationStep("ruff-format-check", "python -m ruff format --check .", "format", 1, False, scope_hint="repo", language_family="python", target_strategy="changed_files", escalation_role="early_signal", typical_trigger_conditions=["python_changed", "formatting_only"]))
        steps.append(ValidationStep("ruff-check", "python -m ruff check .", "lint", 1, False, scope_hint="repo", language_family="python", target_strategy="changed_files", escalation_role="early_signal", typical_trigger_conditions=["python_changed", "config_changed"]))
        steps.append(ValidationStep("mypy", "python -m mypy .", "typecheck", 2, False, scope_hint="repo", language_family="python", target_strategy="package", escalation_role="medium_gate", typical_trigger_conditions=["python_changed", "api_changed"]))
        if repo_map.test_roots:
            steps.append(ValidationStep("pytest-targeted", "python -m pytest -q", "test", 2, False, scope_hint="targeted", language_family="python", target_strategy="related_tests", escalation_role="targeted_first", typical_trigger_conditions=["test_changed", "source_changed"]))
            steps.append(ValidationStep("pytest", "python -m pytest", "test", 4, False, scope_hint="repo", language_family="python", target_strategy="full", escalation_role="broad_safety_net", typical_trigger_conditions=["manifest_changed", "dependency_changed", "broad_change"]))
    if "javascript" in repo_map.languages or "typescript" in repo_map.languages:
        steps.append(ValidationStep("npm-lint", "npm run lint --if-present", "lint", 2, False, scope_hint="repo", language_family="node", target_strategy="full", escalation_role="early_signal", typical_trigger_conditions=["node_changed", "manifest_changed"]))
        steps.append(ValidationStep("npm-test", "npm test --if-present", "test", 4, False, scope_hint="repo", language_family="node", target_strategy="full", escalation_role="broad_safety_net", typical_trigger_conditions=["node_changed", "dependency_changed"]))
    if not steps:
        steps.append(ValidationStep("git-diff", "git diff --stat", "inspection", 1, False, scope_hint="repo", language_family="generic", target_strategy="none", escalation_role="fallback", typical_trigger_conditions=["unknown"]))
    return sorted(steps, key=lambda s: (s.cost_level, s.kind, s.name))


def scan_repo(repo: Path) -> tuple[RepoMap, ValidationConfig, ProjectRules]:
    discovery = _iter_repo_paths(repo)
    manifests = sorted([p for p in discovery.files if Path(p).name in MANIFEST_FILES and not Path(p).name.endswith("lock")])
    lockfiles = sorted([p for p in discovery.files if Path(p).name in {"poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}])
    config_files = sorted([p for p in discovery.files if Path(p).name in CONFIG_HINT_FILES])
    docs_roots = sorted({"docs" for f in discovery.files if f.startswith("docs/") or f == "docs"})
    if any(Path(f).name.lower() in {"readme.md", "readme.rst"} for f in discovery.files):
        docs_roots = sorted(set(docs_roots) | {"."})

    source_roots = _source_roots(repo, discovery)
    test_roots = _test_roots(discovery)
    package_roots = sorted({r for r in source_roots if (repo / r / "__init__.py").exists() or (repo / r).is_dir()})[:12]

    repo_map = RepoMap(
        languages=_infer_languages(discovery, manifests + lockfiles),
        frameworks=_infer_frameworks(discovery.files, manifests + lockfiles, config_files),
        package_managers=_package_managers(manifests, lockfiles),
        package_roots=package_roots,
        source_roots=source_roots,
        test_roots=test_roots,
        docs_roots=docs_roots,
        manifests=manifests,
        lockfiles=lockfiles,
        config_files=config_files,
        major_configs=sorted((config_files + manifests + lockfiles)[:16]),
        likely_entrypoints=sorted([p for p in ENTRYPOINT_HINTS if (repo / p).exists()]),
        build_system_hints=sorted([n for n in ["make" if (repo / "Makefile").exists() else "", "just" if (repo / "justfile").exists() else ""] if n]),
        ci_hints=sorted([f for f in discovery.files if f.startswith(".github/workflows/")][:8]),
        generated_code_hints=sorted([d for d in discovery.directories if "generated" in d or d.endswith("/gen")][:8]),
        package_test_relationships=_package_test_relationships(source_roots, test_roots),
        source_test_patterns=_source_test_patterns(source_roots, test_roots),
        repo_shape=_repo_shape(source_roots, manifests + lockfiles),
        summary={
            "file_count": len(discovery.files),
            "directory_count": len(discovery.directories),
            "top_level_entries": sorted({Path(p).parts[0] for p in discovery.files})[:16],
        },
    )
    repo_map.validation_toolchain_hints = sorted({s.name for s in _build_validation_steps(repo_map)})

    validation = ValidationConfig(_build_validation_steps(repo_map))
    rules = [
        f"Primary source roots: {', '.join(repo_map.source_roots[:4]) or 'none detected'}.",
        f"Primary test roots: {', '.join(repo_map.test_roots[:4]) or 'none detected'}.",
        "Run validation steps from .villani/validation.json in cost order with targeted-first testing.",
        "Escalate to broader validation when manifests/configs/dependencies change.",
        "Keep .villani memory compact and deterministic.",
    ]
    return repo_map, validation, ProjectRules(rules=rules[:8])


def init_project_memory(repo: Path) -> dict[str, Path]:
    from villani_code.session_state import SessionMemory, save_session_state

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
    save_session_state(repo, SessionMemory())
    return files


def ensure_project_memory(repo: Path) -> dict[str, Path]:
    root = repo / VILLANI_DIR
    required = [root / "project_rules.md", root / "validation.json", root / "repo_map.json", root / "session_state.json"]
    if any(not p.exists() for p in required):
        return init_project_memory(repo)
    return {"project_rules": required[0], "validation": required[1], "repo_map": required[2], "session_state": required[3]}


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


def update_session_state(repo: Path, state: SessionState):
    from villani_code.session_state import (
        session_memory_from_project_state,
        update_session_state as update_persistent_session_state,
    )

    return update_persistent_session_state(repo, session_memory_from_project_state(state))
